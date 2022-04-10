import numpy as np
from . import distributions, utils

#
#   Approximate Posterior Entropy
#

class APE:

    def __init__(self, prior, likelihood, posterior, apply_control_variates=True, use_reparameterisation=False):
        if use_reparameterisation:
            self._loss_and_grad = self._create_reparameterisation_loss(prior, likelihood, posterior)
        else:
            self._loss_and_grad = self._create_loss(prior, likelihood, posterior, apply_control_variates)

    def __call__(self, d, num_samples=None, samples=None, rng=None, return_grad=True):
        if (num_samples is None) and (samples is None):
            raise ValueError('Must specify either num_samples or samples as an input.')
        if num_samples is None:
            # Get sample dimension of first element of samples dict:
            num_samples = list(samples.values())[0].shape[0]
        if samples is None:
            samples = {}
        return self._loss_and_grad(d, num_samples, samples, rng, return_grad)

    @classmethod
    def using_laplace_approximation(cls, model, minimizer, prior_mean, prior_cov, noise_cov, apply_control_variates=True, use_reparameterisation=False):
        prior = distributions.Prior.gaussian(prior_mean, prior_cov)
        likelihood = distributions.Likelihood.from_model_plus_constant_gaussian_noise(model, noise_cov)
        approx_posterior = distributions.Posterior.laplace_approximation(model, minimizer, noise_cov, prior_mean, prior_cov)
        return cls(prior, likelihood, approx_posterior, apply_control_variates, use_reparameterisation)

    @staticmethod
    def _create_reparameterisation_loss(prior, likelihood, posterior):
        
        def ape_and_grad(d, num_samples, samples, rng, return_grad):
            if 'theta' in samples:
                theta = samples['theta']
            else:
                theta = prior.sample(num_samples, rng) # shape = (num_samples, theta_dim)
            if 'epsilon' in samples:
                epsilon = samples['epsilon']
            else:
                epsilon = likelihood.sample_base(num_samples, rng)
            transform = likelihood.transform(epsilon, theta, d, return_dd=return_grad)
            post_vals = posterior.logpdf(theta, transform['y'], d, return_dd=return_grad, return_dy=return_grad)
            loss_samples = post_vals['logpdf']
            if return_grad:
                grad_samples = np.einsum('aij,ai->aj', transform['y_dd'], post_vals['logpdf_dy']) + post_vals['logpdf_dd']
            loss = np.mean(loss_samples)
            if return_grad:
                loss_del_d = np.mean(grad_samples, axis=0)
            return loss if not return_grad else (loss, loss_del_d)

        return ape_and_grad

    @staticmethod
    def _create_loss(prior, likelihood, posterior, apply_control_variates):
        
        def ape_and_grad(d, num_samples, samples, rng, return_grad):
            outputs = {}
            if 'theta' in samples:
                theta = samples['theta']
            else: 
                theta = prior.sample(num_samples, rng) # shape = (num_samples, theta_dim)
            if ('theta' in samples) and ('y' in samples):
                y = samples['y']
            else:
                y = likelihood.sample(theta, d, num_samples, rng) # shape = (num_samples, y_dim)
            post_vals = posterior.logpdf(theta, y, d, return_dd=return_grad)
            outputs['loss'] = post_vals['logpdf']
            # Need to compute like_grad if we're applying control variates:
            if return_grad or apply_control_variates:
                like_grad = likelihood.logpdf(y, theta, d, return_logpdf=False, return_dd=True)['logpdf_dd']
            if return_grad:
                outputs['loss_del_d'] = np.einsum('a,ai->ai', post_vals['logpdf'], like_grad) + post_vals['logpdf_dd']
            for key, val in outputs.items():
                if apply_control_variates:
                    outputs[key] = utils.apply_control_variates(val, cv=like_grad)
                else:
                    outputs[key] = np.mean(val, axis=0)
            return outputs['loss'] if not return_grad else (outputs['loss'], outputs['loss_del_d'])

        return ape_and_grad

#
#   'Alphabet' Optimal Criteria
#

class _Alphabet:
    
    def __init__(self, cov_func):
        self._loss_and_grad = self._create_loss_and_grad(cov_func)

    def __call__(self, d, theta_estimate, num_samples, rng=None, return_grad=True):
        return self._loss_and_grad(d, theta_estimate, num_samples, rng, return_grad)

class D_Optimal(_Alphabet):

    @staticmethod
    def _create_loss_and_grad(cov_func):
        def loss_and_grad(d, theta_estimate, num_samples, rng, return_grad):
            cov_vals = cov_func(d, theta_estimate, num_samples, rng, return_dd=return_grad)
            cov = cov_vals['cov']
            loss = -1*np.linalg.det(cov)
            if return_grad:
                # Derivative of det(M) wrt M - see Eqn (49) in Matrix Cookbook (https://www2.imm.dtu.dk/pubdb/edoc/imm3274.pdf):
                cov_dd = cov_vals['cov_dd']
                loss_del_cov = loss*np.linalg.inv(cov).T
                loss_del_d = np.einsum('ij,ijk->k', loss_del_cov, cov_dd)
            return loss if not return_grad else loss, loss_del_d
        return loss_and_grad

class A_Optimal(_Alphabet):

    @staticmethod
    def _create_loss_and_grad(cov_func):
        def loss_and_grad(d, theta_estimate, num_samples, rng, return_grad):
            cov_vals = cov_func(d, theta_estimate, num_samples, rng, return_dd=return_grad)
            cov = cov_vals['cov']
            inv_cov = np.linalg.inv(cov)
            loss = -1*np.trace(cov)
            if return_grad:
                # Derivative of tr(M^-1) wrt M = -((M^-1)^T)@((M^-1)^T) - substitute A = B = I into Eqn (124) in Matrix Cookbook:
                cov_dd = cov_vals['cov_dd']
                loss_del_cov = -1*np.einsum('ji,kj->ik', inv_cov, inv_cov)
                # Want to MAXIMISE trace of inverse cov:
                loss_del_d = -1*np.einsum('ij,ijk->k', loss_del_cov, cov_dd)
            return loss if not return_grad else loss, loss_del_d
        return loss_and_grad

class E_Optimal(_Alphabet):

    @staticmethod
    def _create_loss_and_grad(cov_func):
        def loss_and_grad(d, theta_estimate, num_samples, rng, return_grad):
            cov_vals = cov_func(d, theta_estimate, num_samples, rng, return_dd=return_grad)
            cov = cov_vals['cov']
            eigvals, eigvecs = np.linalg.eigh(cov)
            loss, min_eigvec = -1*eigvals[0], eigvecs[:,0]
            if return_grad:
                # Derivative of eigenvalue wrt matrix - see https://math.stackexchange.com/questions/2588473/derivatives-of-eigenvalues
                cov_dd = cov_vals['cov_dd']
                loss_del_cov = np.einsum('i,j->ij', min_eigvec, min_eigvec)
                # Need to MAXIMISE the smallest eigenvalue:
                loss_del_d = -1*np.einsum('ij,ijk->k', loss_del_cov, cov_dd)
            return loss if not return_grad else loss, loss_del_d
        return loss_and_grad
