from __future__ import absolute_import, division, print_function
import numpy as np
import scipy.optimize
import logging
import os.path
import ad

from . import estimation_tools

logger = logging.getLogger(__name__)

class PopulationOptimizer(object):
    _npop = 1
    def __init__(self, iserv, outdir):
        self._iserv = iserv
        self._bounds = iserv.bounds
        self._outdir = outdir
        self._precond = iserv.precond
        self._K = self._iserv.model[0].K
        self._coords = [(i, cc) for i, m in enumerate(self._iserv.model) for cc in m.coords]
        for m in self._iserv.model:
            if m.K != self._K:
                raise RuntimeError("models need to have same time periods and change points")

    def run(self, niter, fix_rho):
        iserv = self._iserv
        logger.debug("Initializing model(s)")
        models = iserv.model
        iserv.model = models
        logger.debug("Performing initial E step")
        iserv.E_step()
        llold = np.mean([x for loglik in iserv.loglik() for x in loglik])
        logger.debug("Starting loglik: %g" % llold)
        for i in range(niter):
            logger.info("EM iteration %d/%d" % (i + 1, niter))
            logger.info("\tM-step...")
            if not fix_rho:
                self._optimize_param("rho")
            # self._optimize_param("theta")
            self._optimize(models)
            logger.info("Current model(s):")
            for j, m in enumerate(models, 1):
                logger.info("Pop %d:\n%s" % (j, np.array_str(np.array(m.x[:2]).astype(float), precision=2)))
            iserv.model = models
            logger.info("\tE-step...")
            iserv.E_step()
            ll = np.mean([x for loglik in iserv.loglik() for x in loglik])
            logger.info("\tNew/old loglik: %f/%f" % (ll, llold))
            if ll < llold:
                logger.warn("Log-likelihood decreased")
            llold = ll
            iserv.dump([[os.path.join(self._outdir, ".pop%d.iter%d" % (j, i))] for j in range(len(models))])
        ## Optimization concluded
        iserv.dump([[os.path.join(self._outdir, "pop%d.final" % j)] for j in range(len(models))])
        return llold

    def _f_param(self, x, param):
        x0 = x[0]
        x = ad.adnumber(x0)
        setattr(self._iserv, param, x)
        q = -np.mean(self._iserv.Q())
        logger.debug("f_%s: q(%f)=%f" % (param, x0, q.x))
        return (q.x, np.array([q.d(x)]))

    def _f(self, xs, models):
        xs = ad.adnumber(xs)
        for i, xx in enumerate(xs):
            xx.tag = i
        for i, (a, cc) in enumerate(self._coords):
            models[a][cc] = xs[i] * models[a].precond[cc]
        self._pre_Q(models)
        self._iserv.model = models
        q = self._iserv.Q()
        reg = np.mean(self._iserv.penalize(models))
        ll = -np.mean([sum(qq) for qq in q])
        ll += reg
        ret = [ll.x, np.array(list(map(ll.d, xs)))]
        logger.debug(xs)
        logger.debug(ret)
        return ret

    def _optimize_param(self, param):
        logger.debug("Updating %s" % param)
        if param == "theta":
            d = (3, -1)
        elif param == "rho":
            d = (4, -1)
        else:
            raise RuntimeError("unrecognized param")
        self._iserv.derivatives = [[d]] * self._npop
        x0 = getattr(self._iserv, param)
        # f0, fp = self._f_param(x0, param)
        # for i in range(len(x0)):
        #      x0c = x0.copy()
        #      x0c[i] += 1e-8
        #      f1, _ = self._f_param(x0c, param)
        #      logger.info((i, f1, f0, (f1 - f0) / 1e-8, fp[i]))
        bounds = [(1e-6, 1e-2)]
        # res = scipy.optimize.fmin_tnc(self._f_param, x0, None, args=(param,), bounds=bounds)
        def _f(x):
            return self._f_param([x], param)[0]
        logger.info("old %s: f(%g)=%g" % (param, x0, _f(x0)))
        x = scipy.optimize.fminbound(_f, *(bounds[0]))
        logger.info("new %s: f(%g)=%g" % (param, x, _f(x)))
        setattr(self._iserv, param, x)

    def _optimize(self, models):
        logger.debug("Performing a round of optimization")
        x0 = np.array([float(models[i][cc] / models[i].precond[cc]) for i, cc in self._coords])
        d = [[],[]]
        self._iserv.derivatives = d
        logger.info("old model: f(m)=%g" % self._f(x0, models)[0])
        for i, cc in self._coords:
            d[i].append(cc)
        self._iserv.derivatives = d
        # logger.info("gradient check")
        # f0, fp = self._f(x0, models)
        # for i in range(len(x0)):
        #     x0c = x0.copy()
        #     x0c[i] += 1e-8
        #     f1, _ = self._f(x0c, models)
        #     logger.info((i, cc, f1, f0, (f1 - f0) / 1e-8, fp[i]))
        # logger.info(scipy.optimize.check_grad(lambda x: self._f(x, models)[0], lambda x: self._f(x, models)[1], x0))
        res = scipy.optimize.fmin_l_bfgs_b(self._f, x0, None, args=[models], 
                bounds=[tuple(self._bounds[cc] / models[i].precond[cc]) for i, cc in self._coords],
                factr=1e9)
        for xx, (i, cc) in zip(res[0], self._coords):
            models[i][cc] = xx * models[i].precond[cc]
        logger.info("new model: f(m)=%g" % res[1])
        self._post_optimize(models)
        self._iserv.model = models

    def _pre_Q(self, models):
        pass

    def _post_optimize(self, models):
        pass

class TwoPopulationOptimizer(PopulationOptimizer):
    _npop = 2
    def _join_before_split(self, models):
        for a, cc in self._coords:
            if a == 0 and cc[1] >= self._split:
                models[1][cc] = models[0][cc]

    # Alias these methods to fix stuff before and after split
    _pre_Q = _post_optimize = _join_before_split

    def run(self, niter, fix_rho):
        upper = 2 * self._K
        lower = 0
        self._split = self._K
        self._old_aic = np.inf
        i = 1
        models = self._iserv.model
        while True:
            logger.info("Outer iteration %d / split point %d" % (i, self._split))
            self._coords = [(0, cc) for cc in models[0].coords]
            self._coords += [(1, cc) for cc in models[1].coords if cc[1] < self._split]
            # reset models
            self._iserv.reset()
            ll = PopulationOptimizer.run(self, niter, fix_rho)
            aic = 2 * (len(self._coords) - ll)
            logger.info((len(self._coords), ll, aic))
            logger.info("AIC old/new: %g/%g" % (self._old_aic, aic))
            if aic < self._old_aic:
                upper = self._split
            else:
                lower = self._split
            self._old_aic = aic
            new_split = int(0.5 * (upper + lower))
            if abs(new_split - self._split) == 1:
                break
            self._split = new_split
            i += 1
        s = self._iserv.model[0][2]
        logger.info("split chosen to be: [%g, %g)" % (s[self._split], s[self._split + 1]))
