import sys
import time

import torch
import torch.nn.functional as F
from torch import tensor
from torch.optim import Adam
from sklearn.model_selection import StratifiedKFold
from torch_geometric.data import DataLoader, DenseDataLoader as DenseLoader
from view_generator import Generator
from utils import print_weights
from tqdm import tqdm
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')


def single_train_test(train_dataset,
                      test_dataset,
                      model_func,
                      epochs,
                      batch_size,
                      lr,
                      lr_decay_factor,
                      lr_decay_step_size,
                      weight_decay,
                      epoch_select,
                      with_eval_mode=True):
    assert epoch_select in ['test_last', 'test_max'], epoch_select

    model = model_func(train_dataset).to(device)
    print_weights(model)
    optimizer = Adam(model.parameters(), lr=lr, weight_decay=weight_decay)

    train_loader = DataLoader(train_dataset, batch_size, shuffle=True)
    test_loader = DataLoader(test_dataset, batch_size, shuffle=False)
    train_accs, test_accs = [], []
    t_start = time.perf_counter()
    for epoch in range(1, epochs + 1):
        if torch.cuda.is_available():
            torch.cuda.synchronize()

        train_loss, train_acc = train(
            model, optimizer, train_loader, device)
        train_accs.append(train_acc)
        test_accs.append(eval_acc(model, test_loader, device, with_eval_mode))

        if torch.cuda.is_available():
            torch.cuda.synchronize()

        print('Epoch: {:03d}, Train Acc: {:.4f}, Test Acc: {:.4f}'.format(
            epoch, train_accs[-1], test_accs[-1]))
        sys.stdout.flush()

        if epoch % lr_decay_step_size == 0:
            for param_group in optimizer.param_groups:
                param_group['lr'] = lr_decay_factor * param_group['lr']

    t_end = time.perf_counter()
    duration = t_end - t_start

    if epoch_select == 'test_max':
        train_acc = max(train_accs)
        test_acc = max(test_accs)
    else:
        train_acc = train_accs[-1]
        test_acc = test_accs[-1]

    return train_acc, test_acc, duration


from copy import deepcopy
def cross_validation_with_val_set(dataset,
                                  model_func,
                                  folds,
                                  epochs,
                                  batch_size,
                                  lr,
                                  lr_decay_factor,
                                  lr_decay_step_size,
                                  weight_decay,
                                  epoch_select,
                                  with_eval_mode=True,
                                  logger=None,
                                  dataset_name=None,
                                  aug1=None, aug_ratio1=None,
                                  aug2=None, aug_ratio2=None, 
                                  dim = None,
                                  suffix=None):
    assert epoch_select in ['val_max', 'test_max'], epoch_select

    val_losses, train_accs, test_accs, durations = [], [], [], []
    for fold, (train_idx, test_idx, val_idx) in enumerate(
            zip(*k_fold(dataset, folds, epoch_select))):

        """
        train_dataset = dataset[train_idx]
        test_dataset = dataset[test_idx]
        val_dataset = dataset[val_idx]

        train_loader = DataLoader(train_dataset, batch_size, shuffle=True)
        val_loader = DataLoader(val_dataset, batch_size, shuffle=False)
        test_loader = DataLoader(test_dataset, batch_size, shuffle=False)
        """

        dataset.aug = "none"
        model = model_func(dataset).to(device)
        if fold == 0:
            print('----------MODEL PARAMETER----------')
            print_weights(model)
        optimizer = Adam(model.parameters(), lr=lr, weight_decay=weight_decay)

        if torch.cuda.is_available():
            torch.cuda.synchronize()

        t_start = time.perf_counter()

        gen = Generator(device, dim, dataset.num_features).to(device)
        if fold == 0:
            print('----------GEN PARAMETER----------')
            print_weights(gen)
        view_optimizer = torch.optim.Adam(gen.parameters(),lr=lr,weight_decay=weight_decay)

        for epoch in range(1, epochs + 1):
             
            train_loss, _ = train(
                model, optimizer, gen, view_optimizer, dataset, device, batch_size, aug1, aug_ratio1, aug2, aug_ratio2)

            print(train_loss)
            """
            train_loss, train_acc = train(
                model, optimizer, train_loader, device)
            train_accs.append(train_acc)
            val_losses.append(eval_loss(
                model, val_loader, device, with_eval_mode))
            test_accs.append(eval_acc(
                model, test_loader, device, with_eval_mode))
            eval_info = {
                'fold': fold,
                'epoch': epoch,
                'train_loss': train_loss,
                'train_acc': train_accs[-1],
                'val_loss': val_losses[-1],
                'test_acc': test_accs[-1],
            }

            if logger is not None:
                logger(eval_info)
            """

            if epoch % lr_decay_step_size == 0:
                for param_group in optimizer.param_groups:
                    param_group['lr'] = lr_decay_factor * param_group['lr']

            with open('logs/' + dataset_name + '_' + aug1 + '_' + str(aug_ratio1) + '_'+ aug2 + '_' + str(aug_ratio2) + '_cl_log', 'a+') as f:
                 f.write(str(epoch) + ' ' + str(train_loss))
                 f.write('\n')

            if epoch % 20 == 0:
                 torch.save(model.state_dict(), 'models/' + dataset_name + '_' + aug1 + '_' + str(aug_ratio1) + '_'+ aug2 + '_' + str(aug_ratio2) + '_' + str(epoch) + '_' + str(lr) + '_' + str(suffix)  + '.pt')

        print("finish run")
        break

        if torch.cuda.is_available():
            torch.cuda.synchronize()

        t_end = time.perf_counter()
        durations.append(t_end - t_start)

    """
    duration = tensor(durations)
    train_acc, test_acc = tensor(train_accs), tensor(test_accs)
    val_loss = tensor(val_losses)
    train_acc = train_acc.view(folds, epochs)
    test_acc = test_acc.view(folds, epochs)
    val_loss = val_loss.view(folds, epochs)
    if epoch_select == 'test_max':  # take epoch that yields best test results.
        _, selected_epoch = test_acc.mean(dim=0).max(dim=0)
        selected_epoch = selected_epoch.repeat(folds)
    else:  # take epoch that yields min val loss for each fold individually.
        _, selected_epoch = val_loss.min(dim=1)
    test_acc = test_acc[torch.arange(folds, dtype=torch.long), selected_epoch]
    train_acc_mean = train_acc[:, -1].mean().item()
    test_acc_mean = test_acc.mean().item()
    test_acc_std = test_acc.std().item()
    duration_mean = duration.mean().item()

    print('Train Acc: {:.4f}, Test Acc: {:.3f} ± {:.3f}, Duration: {:.3f}'.
          format(train_acc_mean, test_acc_mean, test_acc_std, duration_mean))
    sys.stdout.flush()

    return train_acc_mean, test_acc_mean, test_acc_std, duration_mean
    """


def k_fold(dataset, folds, epoch_select):
    skf = StratifiedKFold(folds, shuffle=True, random_state=12345)

    test_indices, train_indices = [], []
    for _, idx in skf.split(torch.zeros(len(dataset)), dataset.data.y):
        test_indices.append(torch.from_numpy(idx))

    if epoch_select == 'test_max':
        val_indices = [test_indices[i] for i in range(folds)]
    else:
        val_indices = [test_indices[i - 1] for i in range(folds)]

    for i in range(folds):
        train_mask = torch.ones(len(dataset), dtype=torch.uint8)
        train_mask[test_indices[i].long()] = 0
        train_mask[val_indices[i].long()] = 0
        train_indices.append(train_mask.nonzero().view(-1))

    return train_indices, test_indices, val_indices


def num_graphs(data):
    if data.batch is not None:
        return data.num_graphs
    else:
        return data.x.size(0)


def train(model, optimizer, gen, view_optimizer, dataset, device, batch_size, aug1, aug_ratio1, aug2, aug_ratio2):

    dataset.aug = "pre"
    imp_batch_size = 16
    loader = DataLoader(dataset, batch_size=imp_batch_size, shuffle=False)
    model.eval()
    torch.set_grad_enabled(False)

    print('Lipschitz Computation Begin!')
    
    for step, batch in enumerate(tqdm(loader)):
        node_index_start = step * imp_batch_size
        node_index_end = min(node_index_start + imp_batch_size - 1, len(dataset) - 1)
        data = batch.to(device)
        nodes_imp, nodes_Lipschitz = gen.Node_Lipschitz(data)
        nodes_imp = nodes_imp.detach()
        nodes_Lipschitz = nodes_Lipschitz.detach()
        dataset.node_score[dataset.slices['x'][node_index_start]:dataset.slices['x'][node_index_end + 1]] = \
            torch.squeeze(nodes_imp)
        dataset.node_Lipschitz[dataset.slices['x'][node_index_start]:dataset.slices['x'][node_index_end + 1]] = \
            torch.squeeze(nodes_Lipschitz)
        
    print(' ')
    print('Lipschitz Computation Completed!')
    print('================')


    dataset1 = deepcopy(dataset)
    dataset1 = dataset1.shuffle()
    dataset2 = deepcopy(dataset1)
    dataset3 = deepcopy(dataset1)

    #dataset1.aug, dataset1.aug_ratio = 'none', aug_ratio1
    dataset2.aug, dataset2.aug_ratio = aug1, aug_ratio1
    dataset3.aug, dataset3.aug_ratio = aug2, aug_ratio2

    loader1 = DataLoader(dataset1, batch_size, shuffle=False)
    loader2 = DataLoader(dataset2, batch_size, shuffle=False)
    loader3 = DataLoader(dataset3, batch_size, shuffle=False)

    torch.set_grad_enabled(True)
    model.train()

    total_loss = 0
    correct = 0
    for step, batch in enumerate(tqdm(zip(loader1, loader2, loader3))):
        batch1, batch2, batch3 = batch
        
        batch1 = batch1.to(device)
        batch2 = batch2.to(device)
        batch3 = batch3.to(device)

        optimizer.zero_grad()
        view_optimizer.zero_grad()

        data_imp = gen.explain(batch1)
        L = batch1.node_weight.reshape(-1, 1)
        data_imp = data_imp * (1 - L) + L
        out1 = model.forward_cl(batch1, data_imp)

        data_aug_imp = gen.explain(batch2)
        out2 = model.forward_cl(batch2, data_aug_imp)

        data_cp_imp = gen.explain(batch3)
        out3 = model.forward_cl(batch3, data_cp_imp)

        loss = model.loss_ra(out1, out2, out3)
        loss.backward()
        total_loss += loss.item() * num_graphs(batch1)
        optimizer.step()
        view_optimizer.step()
    return total_loss / len(loader1.dataset), 0


def eval_acc(model, loader, device, with_eval_mode):
    if with_eval_mode:
        model.eval()

    correct = 0
    for data in loader:
        data = data.to(device)
        with torch.no_grad():
            pred = model(data).max(1)[1]
        correct += pred.eq(data.y.view(-1)).sum().item()
    return correct / len(loader.dataset)


def eval_loss(model, loader, device, with_eval_mode):
    if with_eval_mode:
        model.eval()

    loss = 0
    for data in loader:
        data = data.to(device)
        with torch.no_grad():
            out = model(data)
        loss += F.nll_loss(out, data.y.view(-1), reduction='sum').item()
    return loss / len(loader.dataset)
