import os
import os.path as osp
import re
import random
import numpy as np
from PIL import Image

import torch
from torch.utils.data import Dataset
from torchvision.datasets.folder import default_loader
import torchvision.transforms.functional as F

from file_utils import load_pickle, read_lines
from kpt_to_pap_mask import gen_pap_masks
from fuse_parts import fuse_parts


def list_pictures(directory, ext='jpg|jpeg|bmp|png|ppm'):
    return sorted([os.path.join(root, f)
                   for root, _, files in os.walk(directory) for f in files
                   if re.match(r'([\w]+\.(?:' + ext + '))', f)])


class MSMT17(Dataset):
    """
    Attributes:
        imgs (list of str): dataset image file paths
        _id2label (dict): mapping from person id to softmax continuous label
    """

    @staticmethod
    def id(file_path):
        """
        :param file_path: unix style file path
        :return: person id
        """
        im_name = osp.basename(file_path)
        id = int(im_name[:4])
        cam = int(im_name[9:11])
        return id

    @staticmethod
    def camera(file_path):
        """
        :param file_path: unix style file path
        :return: camera id
        """
        im_name = osp.basename(file_path)
        id = int(im_name[:4])
        cam = int(im_name[9:11])
        return cam

    @property
    def ids(self):
        """
        :return: person id list corresponding to dataset image paths
        """
        return [self.id(path) for path in self.imgs]

    @property
    def unique_ids(self):
        """
        :return: unique person ids in ascending order
        """
        return sorted(set(self.ids))

    @property
    def cameras(self):
        """
        :return: camera id list corresponding to dataset image paths
        """
        return [self.camera(path) for path in self.imgs]

    def _get_im_paths(self, split):
        if split == 'train':
            im_paths = sorted([osp.join(self.root, 'MSMT17_V1', 'train', l.split(' ')[0]) for l in read_lines(osp.join(self.root, 'MSMT17_V1/list_train.txt'))]) + sorted([osp.join(self.root, 'MSMT17_V1', 'train', l.split(' ')[0]) for l in read_lines(osp.join(self.root, 'MSMT17_V1/list_val.txt'))])
        elif split == 'query':
            im_paths = sorted([osp.join(self.root, 'MSMT17_V1', 'test', l.split(' ')[0]) for l in read_lines(osp.join(self.root, 'MSMT17_V1/list_query.txt'))])
        elif split == 'gallery':
            im_paths = sorted([osp.join(self.root, 'MSMT17_V1', 'test', l.split(' ')[0]) for l in read_lines(osp.join(self.root, 'MSMT17_V1/list_gallery.txt'))])
        else:
            ValueError('Invalid split {}'.format(split))
        return im_paths

    def get_pap_mask(self, im_path):
        key = '/'.join(im_path.split('/')[-4:])
        kpt = self.im_path_to_kpt[key]['kpt']
        kpt[:, 2] = (kpt[:, 2] > 0.1).astype(np.float)
        pap_mask_2p, _ = gen_pap_masks(self.im_path_to_kpt[key]['im_h_w'], (24, 8), kpt, mask_type='PAP_2P')
        pap_mask_3p, _ = gen_pap_masks(self.im_path_to_kpt[key]['im_h_w'], (24, 8), kpt, mask_type='PAP_3P')
        return pap_mask_2p, pap_mask_3p

    def get_ps_label(self, im_path):
        n_seg = 3
        ps_label = Image.open('/'.join([self.ps_dir] + im_path.split('/')[-n_seg:]).replace('.jpg', '.png'))
        if self.ps_fuse_type != 'None':
            ps_label = fuse_parts(ps_label, self.ps_fuse_type)
        ps_label = ps_label.resize(self.ps_w_h, resample=Image.NEAREST)
        return ps_label

    def __init__(self, transform=None, target_transform=None, loader=default_loader, training=None, use_kpt=False, ps_dir=None, split='train', re_obj=None,
                 ps_w_h=(16, 48), ps_fuse_type='None'):
        self.root = 'msmt17'
        self.transform = transform
        self.target_transform = target_transform
        self.loader = loader

        self.imgs = self._get_im_paths(split)

        # convert person id to softmax continuous label
        self._id2label = {_id: idx for idx, _id in enumerate(self.unique_ids)}
        self.training = training
        self.im_path_to_kpt = load_pickle(osp.join(self.root, 'im_path_to_kpt.pkl')) if use_kpt else None
        self.ps_dir = ps_dir
        self.re_obj = re_obj
        self.ps_w_h = ps_w_h
        self.ps_fuse_type = ps_fuse_type

    def __getitem__(self, index):
        path = self.imgs[index]
        target = {'id': self._id2label[self.id(path)]}

        img = self.loader(path)
        if self.im_path_to_kpt is not None:
            target['pap_mask_2p'], target['pap_mask_3p'] = self.get_pap_mask(path)
        if self.ps_dir is not None:
            target['ps_label'] = self.get_ps_label(path)
        if self.training is True:
            if random.random() < 0.5:
                img = F.hflip(img)
                if 'ps_label' in target:
                    target['ps_label'] = F.hflip(target['ps_label'])
        if self.transform is not None:
            img = self.transform(img)
        if 'pap_mask_2p' in target:
            target['pap_mask_2p'], target['pap_mask_3p'] = torch.from_numpy(target['pap_mask_2p']).float(), torch.from_numpy(target['pap_mask_3p']).float()
        if 'ps_label' in target:
            target['ps_label'] = torch.from_numpy(np.array(target['ps_label'])).long()
        if self.training is True:
            img, target['ps_label'] = self.re_obj(img, target['ps_label'])
        return img, target

    def __len__(self):
        return len(self.imgs)
