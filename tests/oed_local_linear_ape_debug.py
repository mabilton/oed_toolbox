import numpy as np
import jax
import jax.numpy as jnp
from oed_sample import create_sampling_funcs
from oed_local_linear import create_local_linear_funcs
import oed_ape
import oed_det

# Make sure Jax running in 64 bit mode - or else finite differences will be very inaccurate:
jax.config.update("jax_enable_x64", True)

# Note that for this example, we have: dim_theta = 2, dim_y = 3, dim_d = 4
def create_linear_model(K, b):
    def linear_model(theta, d):
        theta = jnp.atleast_1d(theta.squeeze())
        y = K(d) @ theta + b(d)
        return jnp.atleast_1d(y.squeeze())
    return linear_model

def get_lin_coeffs():
    def K(d):
        K = jnp.array([-1*(d-5)**2 + 20])
        return K
    def b(d):
        b = jnp.array([0.])
        return b
    return (K, b)

if __name__ == "__main__":
    # Define linear model:
    K, b = get_lin_coeffs()
    lin_model = create_linear_model(K, b)
    # Define noise and prior:
    noise_cov = np.diag([0.1])
    prior_cov = np.diag([1.])
    prior_mean = np.array([5.])
    inv_noise = np.linalg.inv(noise_cov)
    inv_prior = np.linalg.inv(prior_cov)

    # Define bounds for theta and d:
    theta_bounds = np.array([[-10., 10.]])
    d_bounds = np.array([[4., 6.]])

    # Vectorise this function and compute required gradients:
    model_funcs = {}
    model_funcs["g"] = jax.jit(jax.vmap(lin_model, in_axes=(0, 0)))
    lin_model_del_theta = jax.jit(jax.jacrev(lin_model, argnums=0))
    model_funcs["g_del_theta"] = jax.jit(jax.vmap(lin_model_del_theta, in_axes=(0,0)))
    model_funcs["g_del_d"] = jax.jit(jax.vmap(jax.jacrev(lin_model, argnums=1), in_axes=(0,0)))
    model_funcs["g_del_2_theta"] = jax.jit(jax.vmap(jax.jacrev(lin_model_del_theta, argnums=0), in_axes=(0,0)))
    model_funcs["g_del_d_theta"] = jax.jit(jax.vmap(jax.jacrev(lin_model_del_theta, argnums=1), in_axes=(0,0)))

    # Create local_linear functions:
    sample_prior, sample_likelihood = create_sampling_funcs(model_funcs["g"], noise_cov, prior_mean, prior_cov)
    d_dim = 1
    log_probs_and_grads = create_local_linear_funcs(model_funcs, noise_cov, prior_mean, prior_cov, theta_bounds, d_dim)

    # Pass local_linear functions to oed_ape to find optimal d:
    ape_d = oed_ape.find_optimal_d(sample_likelihood, sample_prior, log_probs_and_grads, d_bounds)

    # Compute optimal d by minimising posterior covariance:
    det_funcs = {}
    det_funcs["g_del_theta"] = lambda d : model_funcs["g_del_theta"](jnp.atleast_2d(jnp.array([0.])), d)
    det_funcs["g_del_d_theta"] = lambda d : model_funcs["g_del_d_theta"](jnp.atleast_2d(jnp.array([0.])), d)
    post_det_and_grad = oed_det.create_det_fun(det_funcs, inv_noise, inv_prior)
    det_d = oed_det.find_optimal_d(post_det_and_grad, d_bounds)

    # Compare optimal d results - should be the same:
    print(f"APE Optimal d = {ape_d}, D-Optimal d = {det_d}.")