import jax
import jax.numpy as jnp
import numpy as np

class Model:

    def __init__(self, **model_funcs):
        self._model_funcs = model_funcs

    @classmethod
    def by_finite_differences(model, theta_dim, d_dim, eps, vectorise=True):
        if vectorise:
            model = _vectorise(model)
        model_dt = _finite_diff(model, theta_dim, 0, eps)
        model_dd =  _finite_diff(model, d_dim, 1, eps)
        model_dt_dt = _finite_diff(model_dt, theta_dim, 0, np.sqrt(eps))
        model_dt_dd = _finite_diff(model_dt, d_dim, 1, np.sqrt(eps))
        model_dt_dt_dd = _finite_diff(model_dt_dt, d_dim, 1, np.sqrt(np.sqrt(eps)))
        return cls(model=model, model_dt=model_dt, model_dd=model_dd, model_dt_dt=model_dt_dt,
                   model_dt_dd=model_dt_dd, model_dt_dt_dd=model_dt_dt_dd)

    @classmethod
    def from_jax_function(cls, jax_func, forward_mode=True):

        # Differentiate:
        model_funcs = {'model': jax_func,
                       'model_dt': jax.jacfwd(jax_func, argnums=0),
                       'model_dd': jax.jacfwd(jax_func, argnums=1),
                       'model_dt_dt': jax.jacfwd(jax.jacfwd(jax_func, argnums=0), argnums=0),
                       'model_dt_dd': jax.jacfwd(jax.jacfwd(jax_func, argnums=0), argnums=1),
                       'model_dt_dt_dd': jax.jacfwd(jax.jacfwd(jax.jacfwd(jax_func, argnums=0), argnums=0), argnums=1)}
        
        # Vectorise over sample dimensions and wrap functions:
        for key, func in model_funcs.items():
            model_funcs[key] = cls._wrap_jax_func(jax.vmap(func, in_axes=(0,0)))

        return cls(**model_funcs)

    @staticmethod
    def _wrap_jax_func(func):
        return lambda theta, d : func(jnp.array(theta, dtype=float), jnp.array(d, dtype=float))

    def y(self, theta, d):
        theta, d = self._preprocess_inputs(theta, d)
        num_samples = theta.shape[0]
        return self._model_funcs['model'](theta, d).reshape(num_samples, -1)

    def y_dt(self, theta, d):
        theta, d = self._preprocess_inputs(theta, d)
        num_samples, theta_dim = theta.shape
        return self._model_funcs['model_dt'](theta, d).reshape(num_samples, -1, theta_dim)

    def y_dd(self, theta, d):
        theta, d = self._preprocess_inputs(theta, d)
        num_samples, d_dim = d.shape
        return self._model_funcs['model_dd'](theta, d).reshape(num_samples, -1, d_dim)

    def y_dt_dt(self, theta, d):
        theta, d = self._preprocess_inputs(theta, d)
        num_samples, theta_dim = theta.shape
        return self._model_funcs['model_dt_dt'](theta, d).reshape(num_samples, -1, theta_dim, theta_dim)

    def y_dt_dd(self, theta, d):
        theta, d = self._preprocess_inputs(theta, d)
        (num_samples, theta_dim), d_dim = theta.shape, d.shape[-1]
        return self._model_funcs['model_dt_dd'](theta, d).reshape(num_samples, -1, theta_dim, d_dim)

    def y_dt_dt_dd(self, theta, d):
        theta, d = self._preprocess_inputs(theta, d)
        (num_samples, theta_dim), d_dim = theta.shape, d.shape[-1]
        return self._model_funcs['model_dt_dt_dd'](theta, d).reshape(num_samples, -1, theta_dim, theta_dim, d_dim)

    def _preprocess_inputs(self, theta, d):
        inputs, input_names = [np.atleast_1d(theta), np.atleast_1d(d)], ['theta', 'd']
        inputs = self._ensure_2d_shape(inputs, input_names)
        theta, d = self._check_sample_dimension(inputs, input_names)
        return theta, d

    @staticmethod
    def _ensure_2d_shape(inputs, input_names):
        for idx, (val, val_name) in enumerate(zip(inputs, input_names)):
            if val.ndim == 1:
                inputs[idx] = val[None, :]
            elif val.ndim > 2:
                raise ValueError(f'Expected {input_names} to be either one or two dimensional; ' 
                                 f'instead, it had {val.ndim} dimensions.')
        return inputs

    @staticmethod
    def _check_sample_dimension(inputs, input_names):
        num_samples = np.max([val.shape[0] for val in inputs])
        for idx, (val, val_name) in enumerate(zip(inputs, input_names)):
            if val.shape[0] == 1:
                inputs[idx] = np.broadcast_to(val, (num_samples, val.shape[-1]))
            elif val.shape[0] != num_samples:
                raise ValueError(f'Expected {val_name} input to have a sample dimension of size {num_samples}; ' 
                                 f'instead it was of size {val.shape[0]}.')
        return inputs

#
#   Helper Methods
#

def _vectorise(func):
    def vectorised_func(theta, d):
        num_samples = theta.shape[0]
        if d.shape[0] != num_samples:
            raise ValueError('Zeroth dimension (i.e. the sample dimension) of ' 
                             f'theta (= {theta.shape[0]}) and d (= {d.shape[0]}) do not match.')
        output = []
        for theta_i, d_i in zip(theta, d):
            output.append(func(theta_i, d_i))
        return np.stack(output, axis=0)
    return vectorised_func

def _finite_diff(func, diff_dim, diff_arg, eps, diff_type='centre'):
    
    def func_grad(theta, d):
        theta = create_differentiation_axis(theta, diff_dim) # shape = (num_samples, diff_dim, theta_dim)
        d = create_differentiation_axis(d, diff_dim) # shape = (num_samples, diff_dim, d_dim)
        if diff_arg == 0:
            theta_1, theta_2 = perturb_values(theta, eps, diff_dim, diff_type)
            d_1 = d_2 = d
        else:
            theta_1 = theta_2 = theta
            d_1, d_2 = perturb_values(d, eps, diff_dim, diff_type)
        theta_1, theta_2, d_1, d_2 = collapse_diff_axis(theta_1, theta_2, d_1, d_2) # shape = (num_samples*diff_dim, theta_or_d_dim)
        grad = compute_derivatives(theta_1, theta_2, d_1, d_2, eps, diff_type) # shape = (num_samples*diff_dim, y_dim, ...)
        return reshape_grad_output(grad, diff_dim) # shape = (num_samples, y_dim, ..., diff_dim)

    def create_differentiation_axis(val, diff_dim):
        # Repeat values along new axis - will use new axis to perturb inputs one-at-a-time
        val = np.broadcast_to(val, (diff_dim, *val.shape)) # shape = (diff_dim, num_samples, theta_or_d_dim)
        return np.swapaxes(val, 0, 1) # shape = (num_samples, diff_dim, theta_or_d_dim)
        
    def perturb_values(val, eps, diff_dim, diff_type):
        if diff_type == 'centre':
          val_2 = val + eps*np.identity(diff_dim) # shape = (num_samples, diff_dim, theta_or_d_dim = diff_dim)
          val_1 = val - eps*np.identity(diff_dim)
        elif diff_type == 'backward':
          val_2 = val
          val_1 = val - eps*np.identity(diff_dim)
        elif diff_type == 'forward':
          val_2 = val + eps*np.identity(diff_dim)
          val_1 = val
        else:
          raise ValueError("Invalid diff_type value; can only choose 'centre', 'backward', or 'forward'.")
        return val_1, val_2
        
    def collapse_diff_axis(*vals):
        vals = list(vals)
        for idx, val_i in enumerate(vals):
            vals[idx] = val_i.reshape(-1, val_i.shape[-1]) # shape = (num_samples*diff_dim, theta_or_d_dim)
        return vals

    def compute_derivatives(theta_1, theta_2, d_1, d_2, eps, diff_type):
        # Only theta_1 and theta_2 OR d_1 and d_2 are perturbed:
        denominator = 2*eps if diff_type=='centre' else eps
        return (func(theta_2, d_2) - func(theta_1, d_1))/denominator # shape = (num_samples*diff_dim, y_dim, ...)

    def reshape_grad_output(grad, diff_dim):
        grad = grad.reshape(-1, diff_dim, *grad.shape[1:]) # shape = (num_samples, diff_dim, y_dim, ...)
        return np.moveaxis(grad, 1, -1) # shape = (num_samples, y_dim, ..., diff_dim)

    return func_grad

# class Noise:
