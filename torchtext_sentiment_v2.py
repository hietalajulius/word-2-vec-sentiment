import time
import torch
import torch.nn as nn
import torch.optim as optim
import torchtext
import torchtext.vocab
from torchtext.vocab import GloVe
from torchtext.data import TabularDataset

from embeddings import load_vectors
from utils import epoch_time
from gru import RNNModel, RNNModel2


def binary_accuracy(preds, y):
    """
    Returns accuracy per batch, i.e. if you get 8/10 right, this returns 0.8, NOT 8
    """

    #round predictions to the closest integer
    rounded_preds = torch.round(torch.sigmoid(preds))
    correct = (rounded_preds == y).float() #convert into float for division
    acc = correct.sum() / len(correct)
    return acc


def evaluate_slack(classifier, testloader, print_every, device):
    """
    :param classifier:
    :param testloader:
    :param print_every:
    :return:
    """
    classifier.eval()
    negative = 0
    positive = 0
    total = 0
    pos_scores = {}
    neg_scores = {}

    with torch.no_grad():
        for i, (pad_input_seqs, input_seq_lengths, targets) in enumerate(testloader):
            batch_size = pad_input_seqs.size(1)

            pad_input_seqs = pad_input_seqs.to(device)

            init_hidden = classifier.init_hidden(batch_size, device)
            output = classifier(pad_input_seqs, input_seq_lengths, init_hidden)

            out_flat = output.detach().numpy().argmax(axis=2)
            predicted = torch.tensor(out_flat)

            total += targets.size(0)
            positive += (predicted == 1).sum().item()
            negative += (predicted == 0).sum().item()

            pos_scores.update({i: output.numpy().flatten()[1]})
            neg_scores.update({i: output.numpy().flatten()[0]})

            if (total % print_every == 0):
                print("Counted:", total, "positive", positive / total, "negative", negative / total)
    return positive, negative, total, pos_scores, neg_scores


def evaluate(model, iterator, criterion):
    epoch_loss = 0
    epoch_acc = 0

    model.eval()

    with torch.no_grad():

        for batch in iterator:
            # print(batch.SentimentText)
            if batch.SentimentText.nelement() > 0:

                text_lengths = [len(seq) for seq in batch.SentimentText]
                predictions = model(batch.SentimentText, text_lengths)

                loss = criterion(predictions, batch.Sentiment)

                acc = binary_accuracy(predictions, batch.Sentiment)

                epoch_loss += loss.item()
                epoch_acc += acc.item()
            # else:
            # print(f"Found a non-empty Tensorlist {batch.SentimentText}")

    return epoch_loss / len(iterator), epoch_acc / len(iterator)


def train_epoch(model, iterator, optimizer, criterion, device):
    epoch_loss = 0
    epoch_acc = 0

    model.train()
    #
    for text, y in iterator:
        optimizer.zero_grad()

        # print(f"text is {text}")
        # print(f"text.shape is {text.shape}")
        text_lengths = [len(seq) for seq in text]
        # print(f"text_lengths is {text_lengths}")
        batch_size_var = text.size(0)
        # print(f"batch_size_var {batch_size_var}")


        # model.init_hidden(batch_size_var, device)


        predictions = model(text, text_lengths)
        # predictions = model(batch.SentimentText).squeeze(1)
        loss = criterion(predictions, y)
        acc = binary_accuracy(predictions, y)

        loss.backward()
        optimizer.step()

        epoch_loss += loss.item()
        epoch_acc += acc.item()

    return model, epoch_loss / len(iterator), epoch_acc / len(iterator)


def analyse_sentiments(params=None,
                       model_name=''):
    """
    :param params:
    :param N_EPOCHS:
    :param model_name:
    :return:
    """

    vector_name = params['vectors']
    MAX_VOCAB_SIZE = params['MAX_VOCAB_SIZE']
    min_freq = params['min_freq']
    pretrained = params['pretrained']
    EMBEDDING_DIM = params['embedding_dim']

    FREEZE_EMDEDDINGS = params['RNN_FREEZE_EMDEDDINGS']
    HIDDEN_DIM = params['RNN_HIDDEN_DIM']  # model_params['RNN_HIDDEN_DIM']
    OUTPUT_DIM = 1  # params['OUTPUT_DIM']
    N_LAYERS = params['RNN_N_LAYERS']   # model_params['RNN_N_LAYERS']
    DROPOUT = params['RNN_DROPOUT']   # model_params['RNN_DROPOUT']
    USE_GRU = params['RNN_USE_GRU']  # model_params['RNN_USE_GRU']
    N_EPOCHS = params['RNN_EPOCHS']
    BATCH_SIZE = params['RNN_BATCH_SIZE']


    TEXT = torchtext.data.Field(lower=True,
                                pad_first=True,
                                batch_first=True,
                                init_token='<sos>',
                                eos_token='<eos>'
                                )
    LABEL = torchtext.data.LabelField(dtype=torch.float)
    datafields = [('Sentiment', LABEL), ('SentimentText', TEXT)]
    train_set, val_set, test_set = TabularDataset.splits(path='data/',
                                    train='processed_train.csv',
                                    validation='processed_val.csv',
                                    test='processed_test.csv',
                                    format='csv',
                                    skip_header=True,
                                    fields=datafields)

    if pretrained:
        vectors = load_vectors(fname=vector_name)

        # vectors = GloVe(name="6B", dim=100)
        #vectors = 'glove.twitter.27B.100d'
        TEXT.build_vocab(
                        train_set,
                        vectors=vectors,
                        unk_init=torch.Tensor.normal_
        )
        vectors = TEXT.vocab.vectors
        print(vectors.shape)
        print(vectors.shape[1])
        EMBEDDING_DIM = vectors.shape[1]
    else:
        TEXT.build_vocab(train_set,
                         max_size=MAX_VOCAB_SIZE)

    LABEL.build_vocab(train_set)

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(device)
    # minimise badding for each sentence
    train_iterator, val_iterator, test_iterator = torchtext.data.BucketIterator.splits(
                                                                        (train_set, val_set, test_set),
                                                                        batch_size=BATCH_SIZE,
                                                                        sort_key=lambda x: len(x.SentimentText),
                                                                        sort_within_batch=False,
                                                                        device=device)

    pad_idx = TEXT.vocab.stoi[TEXT.pad_token]
    INPUT_DIM = len(TEXT.vocab)
    print(f"Vocab size is {INPUT_DIM}, emdebbing dim is {EMBEDDING_DIM}")
    model = RNNModel2(vocab_size=INPUT_DIM,
                    embedding_dim=EMBEDDING_DIM,
                    hidden_dim=HIDDEN_DIM,
                    output_dim=OUTPUT_DIM,
                    n_layers=N_LAYERS,
                    bidirectional=True,
                    dropout=DROPOUT,
                    pad_idx=pad_idx,
                    use_gru=USE_GRU)
    print(model)

    if pretrained:
        model.embedding.weight.data.copy_(vectors)

    unk_idx = TEXT.vocab.stoi[TEXT.unk_token]
    init_idx = TEXT.vocab.stoi[TEXT.init_token]
    eos_idx = TEXT.vocab.stoi[TEXT.eos_token]
    model.embedding.weight.data[unk_idx] = torch.zeros(EMBEDDING_DIM)
    model.embedding.weight.data[pad_idx] = torch.zeros(EMBEDDING_DIM)
    model.embedding.weight.data[init_idx] = torch.zeros(EMBEDDING_DIM)
    model.embedding.weight.data[eos_idx] = torch.zeros(EMBEDDING_DIM)

    # freeze embeddings
    if FREEZE_EMDEDDINGS:
        model.embedding.weight.requires_grad = False
    else:
        model.embedding.weight.requires_grad = True

    optimizer = optim.Adam(model.parameters(), lr=1e-3)
    criterion = nn.BCEWithLogitsLoss()
    model = model.to(device)
    criterion = criterion.to(device)

    best_valid_loss = float('inf')
    for epoch in range(N_EPOCHS):
        start_time = time.time()
        model, train_loss, train_acc = train_epoch(model, train_iterator, optimizer, criterion, device)
        valid_loss, valid_acc = evaluate(model, val_iterator, criterion)
        end_time = time.time()

        epoch_mins, epoch_secs = epoch_time(start_time, end_time)

        if valid_loss < best_valid_loss:
            best_valid_loss = valid_loss
            torch.save(model.state_dict(), f"{model_name}.pt")

        print(f'Epoch: {epoch + 1:02} | Epoch Time: {epoch_mins}m {epoch_secs}s')
        print(f'\tTrain Loss: {train_loss:.3f} | Train Acc: {train_acc * 100:.2f}%')
        print(f'\t Val. Loss: {valid_loss:.3f} |  Val. Acc: {valid_acc * 100:.2f}%')

    # Evaluate model performance
    model.load_state_dict(torch.load(f"{model_name}.pt"))
    # print(model)
    test_loss, test_acc = evaluate(model, test_iterator, criterion)
    print(f'Test Loss: {test_loss:.3f} | Test Acc: {test_acc * 100:.2f}%')

    return test_loss, test_acc