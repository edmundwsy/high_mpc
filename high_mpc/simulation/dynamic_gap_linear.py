import numpy as np
#
from high_mpc.simulation.quadrotor import Quadrotor_v0
from high_mpc.simulation.box_v0 import box_v0
from high_mpc.simulation.box_v1 import box_v1
#
from high_mpc.common.quad_index import *

#
class Space(object):

    def __init__(self, low, high):
        self.low = low
        self.high = high
        self.shape = self.low.shape

    def sample(self):
        return np.random.uniform(self.low, self.high)

class DynamicGap2(object):

    def __init__(self, mpc, plan_T, plan_dt):
        #
        self.mpc = mpc
        self.plan_T = plan_T
        self.plan_dt = plan_dt

        # 
        self.goal_point = np.array([5.0,  0.0, 0.0]) 
        self.pivot_point = np.array([0.0, 0.0, 5.0]) # starting point of the box

        # goal state, position, velocity, roll pitch
        self.quad_sT = self.goal_point.tolist() + [0.0, 0.0, 0.0] + [0.0, 0.0] 

        # simulation parameters ....
        self.sim_T = 3.0    # Episode length, seconds
        self.sim_dt = 0.02  # simulation time step
        self.max_episode_steps = int(self.sim_T/self.sim_dt)
        # Simulators, a quadrotor and a pendulum
        self.quad = Quadrotor_v0(dt=self.sim_dt)
        self.pend = box_v0(self.pivot_point, dt=self.sim_dt)

        self.planner = box_v1(pivot_point=self.pivot_point, sigma=10, \
            T=self.plan_T, dt=self.plan_dt)
    

        #
        self.observation_space = Space(
            low=np.array([-10.0, -10.0, -10.0, -2*np.pi, -2*np.pi, -2*np.pi, -10.0, -10.0, -10.0]),
            high=np.array([10.0, 10.0, 10.0, 2*np.pi, 2*np.pi, 2*np.pi, 10.0, 10.0, 10.0]),
        )

        self.action_space = Space(
            low=np.array([0.0]),
            high=np.array([2*self.plan_T])
        )

        # reset the environment
        self.t = 0
        self.reset()
    
    def seed(self, seed):
        np.random.seed(seed=seed)
    
    def reset(self, init_theta=None):
        self.t = 0
        # state for ODE
        self.quad_state = self.quad.reset()
        if init_theta is not None:
            self.pend_state = self.pend.reset(init_theta)
        else:
            self.pend_state = self.pend.reset()
        
        # observation, can be part of the state, e.g., postion
        # or a cartesian representation of the state
        quad_obs = self.quad.get_cartesian_state()
        pend_obs = self.pend.get_cartesian_state()
        #
        obs = (quad_obs - pend_obs).tolist()
        
        return obs

    def step(self, u=0):
        self.t += self.sim_dt
        opt_t = u
        
        print("===========================================================")
        #
        plan_pend_traj, pred_pend_traj_cart = self.planner.plan2(self.pend_state, opt_t, self.quad.get_cartesian_state()) # predict relative pend traj, cartesian pend traj
        # pred_pend_traj_cart = np.array(pred_pend_traj_cart)
        
        #
        quad_state = self.quad.get_cartesian_state()
        pend_state = self.pend.get_cartesian_state()
        # pend_state = np.array([0, 0, 3, 0, 0, 0, 0, 0, 0])
        quad_s0 = np.zeros(8)
        quad_s0[0:3] = quad_state[0:3] - pend_state[0:3]  # relative position
        quad_s0[3:6] = quad_state[6:9] + pend_state[6:9]  # relative velocity # TODO -5
        quad_s0[6:8] = quad_state[3:5]
        quad_s0 = quad_s0.tolist()
        
        # print("quad_s0 pos", quad_s0[0:3])
        # print("quad_s0 vel", quad_s0[3:6])
        # print("quad_s0 rpy", quad_s0[6:8])
        print("quad state vel", quad_state[6:9])
        print("pend state vel", pend_state[6:9])
        print("quad state rpy", quad_state[3:6])
        # ref_traj = quad_s0 + plan_pend_traj + self.quad_sT # in mpc state, 8d+3

        # ------------------------------------------------------------
        # run liear model predictive control
        quad_act, pred_traj = self.mpc.solve(quad_s0) # in relative frame
        # ------------------------------------------------------------
        
        # back to world frame
        pred_traj[:,0:3] = pred_traj[:,0:3] + self.pend.get_cartesian_state()[0:3]
        
        # run the actual control command on the quadrotor
        # if (quad_state[4] > 0.5):
        #     quad_act = np.array([12, 0, 0, 0])
        # else:
        #     quad_act = np.array([9.81, 0, 1.0, 0])
        
        
        self.quad_state = self.quad.run(quad_act)
        # simulate one step pendulum
        self.pend_state = self.pend.run()
        
        # update the observation.
        quad_obs = self.quad.get_cartesian_state()
        print("quad new state pos", quad_obs[0:3])
        print("quad new state euler", quad_obs[3:6])
        pend_obs = self.pend.get_cartesian_state()
        
        obs = (quad_obs - pend_obs).tolist()
        #
        info = {
            "quad_obs": quad_obs, 
            "quad_act": quad_act, 
            "quad_axes": self.quad.get_axes(),
            "pend_obs": pend_obs,
            "pend_corners": self.pend.get_3d_corners(),
            "pred_quad_traj": pred_traj, 
            "pred_pend_traj": pred_pend_traj_cart, 
            "opt_t": opt_t, "plan_dt": self.plan_dt}
        done = False
        if self.t >= (self.sim_T-self.sim_dt):
            done = True

        return obs, 0, done, info
    
    @staticmethod
    def _is_within_gap(gap_corners, point):
        A, B, C = [], [], []    
        for i in range(len(gap_corners)):
            p1 = gap_corners[i]
            p2 = gap_corners[(i + 1) % len(gap_corners)]
            
            # calculate A, B and C
            a = -(p2.y - p1.y)
            b = p2.x - p1.x
            c = -(a * p1.x + b * p1.y)

            A.append(a)
            B.append(b)
            C.append(c)
        D = []
        for i in range(len(A)):
            d = A[i] * point.x + B[i] * point.y + C[i]
            D.append(d)

        t1 = all(d >= 0 for d in D)
        t2 = all(d <= 0 for d in D)
        return t1 or t2

    def close(self,):
        return True

    def render(self,):
        return False
    