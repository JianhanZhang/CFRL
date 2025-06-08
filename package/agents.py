import numpy as np
import torch
import copy

#from utils.utils import glogger
from .utils.base_models import LinearRegressor, NeuralNet

class FQI:
    def __init__(
        self,
        model_type,
        action_space,
        hidden_dims=[32],
        preprocessor=None,
        gamma=0.9,
        learning_rate=0.1,
        epochs=500,
    ):
        self.model_type = model_type
        self.action_space = action_space
        self.action_size = len(action_space)
        self.hidden_dims = hidden_dims
        self.preprocessor = preprocessor
        self.gamma = gamma
        self.learning_rate = learning_rate
        self.epochs = epochs
        self.__name__ = 'FQI'
        self._sanity_check()

    def _sanity_check(self):
        assert self.model_type in ["lm", "nn"], "Invalid model type"

    def _init_model(self, state_dim):
        if self.model_type == "lm":
            self.model = [
                LinearRegressor(
                    featurize_method="polynomial",
                    degree=2,
                    interaction_only=False,
                    is_standarized=False,
                )
                for _ in range(self.action_size)
            ]
        elif self.model_type == "nn":
            self.model = NeuralNet(
                in_dim=state_dim,
                out_dim=self.action_size,
                hidden_dims=self.hidden_dims,
            )

    def _fit_helper(self, states, actions, rewards, next_states, state_dim, max_iter):
        torch.set_num_threads(1)
        states = states.reshape(-1, state_dim)
        next_states = next_states.reshape(-1, state_dim)
        actions = actions.reshape(-1, 1)
        rewards = rewards.reshape(-1, 1)

        for iteration in range(max_iter):
            if self.model_type == "lm":
                self._fit_lm(states, actions, rewards, next_states, iteration)
            elif self.model_type == "nn":
                self._fit_nn(states, actions, rewards, next_states, iteration, state_dim)

    def _fit_nn(self, states, actions, rewards, next_states, iteration, state_dim):

        if iteration == 0:
            Y = rewards
        else:
            self.model.eval()
            with torch.no_grad():
                next_q_values = self.model(torch.FloatTensor(next_states)).numpy()
            Y = (
                rewards + self.gamma * np.max(next_q_values, axis=1, keepdims=True)
            ).reshape(-1, 1)

        new_model = NeuralNet(
            in_dim=state_dim,
            out_dim=self.action_size,
            hidden_dims=self.hidden_dims,
        )

        X = torch.FloatTensor(states)
        Y = torch.FloatTensor(Y)
        actions_tensor = torch.LongTensor(actions.reshape(-1, 1))

        optimizer = torch.optim.Adam(new_model.parameters(), lr=self.learning_rate)
        criterion = torch.nn.MSELoss()

        for _ in range(self.epochs):
            new_model.train()
            optimizer.zero_grad()
            Y_pred = new_model(X).gather(1, actions_tensor)
            loss = criterion(Y_pred, Y)
            loss.backward()
            optimizer.step()
        '''glogger.info(
            f"{iteration}, fqi_nn mse:{loss.item()}, mean_target:{np.mean(Y.detach().numpy())}"
        )'''
        self.model = copy.deepcopy(new_model)

    def _fit_lm(self, states, actions, rewards, next_states, iteration):
        if iteration == 0:
            Y = rewards
        else:
            tmp = np.array(
                [model.predict(next_states) for model in self.model]
            ).T.squeeze()
            Y = (rewards + self.gamma * np.max(tmp, axis=1, keepdims=True)).reshape(
                -1, 1
            )

        new_model = [
            LinearRegressor(
                featurize_method="polynomial",
                degree=2,
                interaction_only=False,
                is_standarized=False,
            )
            for _ in range(self.action_size)
        ]

        for a in range(self.action_size):
            idx = actions.flatten() == a
            new_model[a].fit(states[idx], Y[idx])

        mse = np.mean([m.mse for m in new_model])
        #glogger.info(f"{iteration}, fqi_lm mse:{mse}, mean_target:{np.mean(Y)}")
        self.model = copy.deepcopy(new_model)

    def _act_helper(self, states):
        if self.model_type == "lm":
            q_values = np.array(
                [model.predict(states) for model in self.model]
            ).T.squeeze()
        elif self.model_type == "nn":
            self.model.eval()
            with torch.no_grad():
                q_values = self.model(torch.FloatTensor(states)).numpy()
        return np.argmax(q_values, axis=1)
    
    def train(self, zs, xs, actions, rewards, max_iter, preprocess=True):   
        zs = np.array(zs)
        xs = np.array(xs)
        actions = np.array(actions)
        rewards = np.array(rewards)

        if self.preprocessor is not None and preprocess:
            states_p, rewards_p = self.preprocessor.preprocess_multiple_steps(
                    zs=zs, xs=xs, actions=actions, rewards=rewards
            )
        else:
            states_p = xs
            rewards_p = rewards
        state_dim = states_p.shape[-1]
        self._init_model(state_dim)

        next_states_p = states_p[:, 1:].copy()
        states_p = states_p[:, :-1].copy()

        self._fit_helper(states_p, actions, rewards_p, next_states_p, state_dim, max_iter)

    # FOR A PARTICULAR SEQUENCE, IT SEEMS WE HAVE TO TAKE ACTIONS SEQUENTIALLY FROM THE BEGINNING 
    # TO THE END, AND WE CANNOT SKIP ANY STEP? OTHERWISE INFO IN BUFFER IS MESSED UP. SO TAKEAWAY
    # IS WE CANNOT PREPROCESS AN ARBITRARY SINGLE STEP USIN THE PREPROCESSOR, AND WE MUST 
    # PREPROCESS A WHOLE TRAJECTORY IN ORDER?
    def act(self, z, xt, xtm1=None, atm1=None, uat=None, preprocess=True, **kwargs):
        z = np.array(z)
        xt = np.array(xt)
        if xtm1 is not None:
            xtm1 = np.array(xtm1)
        if atm1 is not None:
            atm1 = np.array(atm1)
        if uat is not None:
            uat = np.array(uat)

        if self.preprocessor is not None and preprocess:
            states = self.preprocessor.preprocess_single_step(
                xt=xt, xtm1=xtm1, z=z, atm1=atm1, rtm1=None
            )
        else:
            states = xt
        actions = self._act_helper(states)
        return actions