import keras
import random
from keras.callbacks import EarlyStopping, ReduceLROnPlateau, ModelCheckpoint
from keras.layers import Dense, Input, LSTM, Embedding, Conv1D, K, Concatenate, Lambda, \
    MaxPooling1D, Flatten
from keras.optimizers import Adam
from keras import Model
import numpy as np
from keras.preprocessing.sequence import pad_sequences
from keras.regularizers import L1L2
from keras.wrappers.scikit_learn import KerasClassifier
from scipy.stats import pearsonr
from sklearn.grid_search import GridSearchCV

from utils.utils import map_strings_to_intarrays
import csv
from sklearn.model_selection import train_test_split
from sklearn.metrics import r2_score

random.seed(9)
smiles_CB1 = []
smiles_CB2 = []
Ki_CB1 = []
Ki_CB2 = []
f = open('Mydata.csv')
csv_f = csv.reader(f)

for row in csv_f:
    # Cannabinoid receptor 1(CB1) ==> Cluster 131
    # Cannabinoid receptor 2(CB2) ==> Cluster 225
    if(row[4] == "Cluster 131" and row[5] != ""):
        smiles_CB1.append(row[3])
        Ki_CB1.append(row[5])
    if(row[4] == "Cluster 225" and row[5] != ""):
        smiles_CB2.append(row[3])
        Ki_CB2.append(row[5])

periodic_elements = ['H', 'Li', 'B', 'C', 'N', 'O', 'F', 'Na', 'Al', 'Si', 'P', 'S', 'Cl',
                       'K', 'Ca', 'V', 'Mn', 'Fe', 'Co', 'Ni', 'Cu', 'Zn', 'As', 'Se', 'Br',
                       'Nb', 'Mo', 'Tc', 'Ru', 'Pd', 'Ag', 'Sn', 'Sb', 'Te', 'I', 'Gd', 'W',
                       'Re', 'Os', 'Pt', 'Au', 'Hg', 'Bi']

smilesAlphabet = list('#%)(+*-/.1032547698:=@[]\\conslr') + periodic_elements + ['se']

def smiles_to_arrays(smiles_list, L=None):
    L = L if L else max([len(x) for x in smiles_list])
    smiles_dict = dict(zip(smilesAlphabet, 1 + np.arange(len(smilesAlphabet))))
    return pad_sequences(map_strings_to_intarrays(smiles_list, smiles_dict), L, padding='post')

class Bioactivity_net(object):
    ENCODING_NAME = 'encoding_layer'

    def __init__(self, max_length=None):
        self.max_length = max_length
        self.n_epochs_patience = 5
        self.n_epochs = 1000
        self.verbose = 1
        self.model = None

    def network_initializer(self, max_timesteps=None):
        raise NotImplementedError("Abstract")

    def fit(self, x, y, x_valid=None, y_valid=None, validation_split=None, batch_size=32):
        if x_valid is None or y_valid is None:
            validation_split = validation_split if validation_split else 1/5.
        x_new = smiles_to_arrays(x, self.max_length)
        K.set_learning_phase(1)

        early_stopping = EarlyStopping(monitor='val_loss', patience=self.n_epochs_patience, verbose=0, mode='min')
        reduce_lr = ReduceLROnPlateau(monitor='loss', patience=self.n_epochs_patience/2, epsilon=1e-3)


        callbacks = [early_stopping, reduce_lr]

        if x_valid is not None and y_valid is not None:
            x_valid_new = smiles_to_arrays(x_valid, self.max_length)
            self.history = self.model.fit(x_new, y,
                                          epochs=self.n_epochs,
                                          batch_size=batch_size,
                                          validation_data=(x_valid_new, y_valid),
                                          callbacks=callbacks,
                                          verbose=0)
        else:
            self.history = self.model.fit(x_new, y,
                                          epochs=self.n_epochs,
                                          batch_size=batch_size,
                                          validation_split=validation_split,
                                          callbacks=callbacks,
                                          verbose=0)
        self.val_loss = early_stopping.best
        K.set_learning_phase(0)
        return self

    def predict(self, x):
        x_new = smiles_to_arrays(x, self.max_length)
        return self.model.predict(x_new).flatten()

class BioactivityLSTM(Bioactivity_net):
    def __init__(self, vocab_size=76, embedding_size=20, layer_sizes=[128],
                 lr=0.001, l2=0.001, dropout=0.5):
        super(BioactivityLSTM, self).__init__()
        self.vocab_size = vocab_size
        self.embedding_size = embedding_size
        self.layer_sizes = layer_sizes
        self.optimizer = Adam(lr)
        self.dropout = dropout
        self.l2 = l2

        self.network_initializer()

    def network_initializer(self, max_timesteps=None):
        ### build encoder
        enc_input = Input(shape=(None, ), dtype='int32', name='input')
        enc_embedding = Embedding(self.vocab_size, self.embedding_size, mask_zero=True, name='embedding')(enc_input)

        # stacking encoding LSTMs
        hidden_states = []
        enc_layer = enc_embedding
        for i, layer_size in enumerate(self.layer_sizes):
            return_sequences = (i != len(self.layer_sizes) - 1)
            enc_layer, hidden_state, cell_state = LSTM(layer_size, return_sequences=return_sequences,
                                                       return_state=True, name='lstm_%d' % (i+1))(enc_layer)
            hidden_states += [hidden_state, cell_state]

        # concatenating LSTMs' states and normalizing their norms
        enc_output = Concatenate()(hidden_states)
        enc_output = Lambda(lambda x: K.l2_normalize(x, axis=1), name=BioactivityLSTM.ENCODING_NAME)(enc_output)

        ### output layer
        out_layer = Dense(1, kernel_regularizer=L1L2(l2=self.l2))(enc_output)
        self.model = Model(inputs=enc_input, outputs=out_layer)
        self.model.compile(optimizer=self.optimizer, loss='mse')
        # self.model.summary()


class BioactivityCNN(Bioactivity_net):
    def __init__(self, max_length, vocab_size=20, embedding_size=20, kernel_sizes=[128], l_pooling=1,
                 lr=0.001, l2=0.001, dropout=0.5):
        super(BioactivityCNN, self).__init__(max_length)
        self.max_length = max_length
        self.vocab_size = vocab_size
        self.embedding_size = embedding_size
        self.kernel_sizes = kernel_sizes
        self.optimizer = Adam(lr)
        self.dropout = dropout
        self.L_pooling = l_pooling
        self.l2 = l2

        self.network_initializer()

    def network_initializer(self, max_timesteps=None):
        ### build encoder
        enc_input = Input(shape=(self.max_length, ), dtype='int32', name='input')
        enc_embedding = Embedding(self.vocab_size, self.embedding_size, name='embedding')(enc_input)

        # stacking encoding LSTMs
        convos = []
        for i, hs in enumerate(self.kernel_sizes):
            if hs > 0:
                convo_layer = Conv1D(
                    filters=hs,
                    kernel_size=i + 1,
                    padding='same')(enc_embedding)
                if self.L_pooling > 1:
                    convo_layer = MaxPooling1D(pool_size=self.L_pooling)(convo_layer)
                convo_layer = Flatten()(convo_layer)
                convos.append(convo_layer)
        enc_output = Concatenate()(convos) if len(convos) > 1 else convos[0]
        enc_output = Lambda(lambda x: K.l2_normalize(x, axis=1), name=BioactivityLSTM.ENCODING_NAME)(enc_output)

        ### output layer

        out_layer = Dense(1, kernel_regularizer=L1L2(l2=self.l2))(enc_output)
        self.model = Model(inputs=enc_input, outputs=out_layer)
        self.model.compile(optimizer=self.optimizer, loss='mse')
        # self.model.summary()


if __name__ == '__main__':
    from sklearn.model_selection import ParameterGrid
    params_dict = dict(
                    emb = [20,40,60,80,100],
                    lay = [[128],[64,64],[128,128],[64,64,64],[128,128,128]],
                    lr_param = [0.01,0.001,0.0001,0.00001],
                    l2_param = [0,0.01,0.001,0.0001])
    for params in ParameterGrid(params_dict):
        x_emb, y_lay, z_lr, k_l2 = params['emb'], params['lay'], params['lr_param'], params['l2_param']
        rgr = BioactivityLSTM(vocab_size=76, embedding_size=x_emb, layer_sizes=y_lay, lr=z_lr, l2=k_l2)
        #rgr = BioactivityCNN(vocab_size=76, max_length=300,embedding_size=x_emb,kernel_sizes=y_lay, lr=z_lr, l2=k_l2)
        x = np.asarray(smiles_CB1)
        y = np.asarray(Ki_CB1, dtype=np.float)
        x_train, x_test, y_train, y_test = train_test_split(x, y, test_size=0.25)
        rgr.fit(x_train, y_train, validation_split=0.25)
        y_pred = rgr.predict(x_test)
        score = r2_score(y_test, y_pred)
        pear = pearsonr(y_test, y_pred)
        print("\nR2 score pour les parametres ", params, "est : ",score,"\n")
        print("PCC score pour les parametres ", params, "est : ",pear, "\n")