import os
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader, random_split
from torchvision import transforms
from PIL import Image
import matplotlib.pyplot as plt
from tqdm import tqdm
from tabulate import tabulate
import re
import numpy as np
import pandas as pd

# 设置一些基本参数
img_height = 224
img_width = 224
num_classes = 361
batch_size = 32
epochs = 50
learning_rate = 0.0001

def angular_distance_compute(a1, a2):
    return 180 - abs(abs(a1 - a2) - 180)


def MAEeval(preds, labels):
    """
    计算 MAE 和 ACC
    :param preds: 模型的预测输出（类别索引）
    :param labels: 真实标签（类别索引）
    :return: 平均绝对误差 (MAE) 和正确率 (ACC, 阈值5°以内)
    """
    errors = []
    for pred, label in zip(preds, labels):
        ang_error = angular_distance_compute(pred.item(), label.item())  # 计算角度误差
        errors.append(ang_error)

    # 计算 MAE 和 ACC
    mae = np.mean(errors)  # 平均绝对误差
    acc = np.mean([error <= 5 for error in errors])  # 阈值5度内的正确率
    return mae, acc



# 数据转换和增广
data_transforms = {
    'train': transforms.Compose([
        transforms.Resize((img_height, img_width)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ]),
    'val': transforms.Compose([
        transforms.Resize((img_height, img_width)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    ]),
}

# 数据集定义
class SoundDataset(Dataset):
    def __init__(self, data_dir, transform=None):
        self.data = []
        self.labels = []
        self.transform = transform
        pattern = re.compile(r'class(\d+)_(\d+)_(\d+)_mic(\d+)\.png')
        for azimuth_dir in os.listdir(data_dir):
            azimuth_path = os.path.join(data_dir, azimuth_dir)
            if os.path.isdir(azimuth_path):
                image_groups = {}
                for filename in os.listdir(azimuth_path):
                    if filename.endswith('.png'):
                        match = pattern.match(filename)
                        if match:
                            sound_class, azimuth, audio_id, mic_id = match.groups()
                            mic_num = int(mic_id) - 1
                            key = f"{sound_class}_{azimuth}_{audio_id}"
                            file_path = os.path.join(azimuth_path, filename)
                            if key not in image_groups:
                                image_groups[key] = [None] * 4  # 更新为4个麦克风
                            image_groups[key][mic_num] = file_path
                for key, images in image_groups.items():
                    if all(image is not None for image in images):
                        self.data.append(images)
                        self.labels.append(int(azimuth_dir.split('_')[-1]))

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        images = self.data[idx]
        label = self.labels[idx]
        loaded_images = []
        for image_path in images:
            with Image.open(image_path) as image:
                if self.transform:
                    image = self.transform(image)
                loaded_images.append(image)
        images = torch.cat(loaded_images, dim=0)  # 拼接为 (channels=12, height, width)
        label = torch.tensor(label, dtype=torch.long)
        return images, label
import timm


class SingleDeiT(nn.Module):
    def __init__(self, num_classes):
        super(SingleDeiT, self).__init__()
        # 加载预训练的 DeiT 模型
        self.base_model = timm.create_model('deit_small_patch16_224', pretrained=True)

        # 修改输入层以适应自定义通道数
        self.base_model.patch_embed.proj = nn.Conv2d(
            12,
            self.base_model.patch_embed.proj.out_channels,
            kernel_size=self.base_model.patch_embed.proj.kernel_size,
            stride=self.base_model.patch_embed.proj.stride,
            padding=self.base_model.patch_embed.proj.padding
        )

        # 修改分类头以适应自定义类别数
        self.base_model.head = nn.Linear(self.base_model.embed_dim, num_classes)

    def forward(self, x):
        # 完整地执行 base_model 的前向传播
        x = self.base_model(x)
        return x



# import timm
#
# # 定义单个 DeiT 网络
# class SingleDeiT(nn.Module):
#     def __init__(self):
#         super(SingleDeiT, self).__init__()
#         # 加载预训练的 DeiT 模型
#         base_model = timm.create_model('deit_small_patch16_224', pretrained=True)
#
#         self.features = nn.Sequential(*list(base_model.children())[:-1])  # 去掉分类层
#         self.embed_dim = base_model.embed_dim  # DeiT Small 的嵌入维度是 384
#
#     def forward(self, x):
#         x = self.features(x)  # 提取特征
#         x = x.mean(dim=1)  # 对 token 维度求平均，得到 (batch_size, embed_dim)
#         return x
#
#
# # 定义多 DeiT 融合模型
# # 定义带注意力机制的多 DeiT 融合模型
# class MultiDeiTWithAttention(nn.Module):
#     def __init__(self, num_classes):
#         super(MultiDeiTWithAttention, self).__init__()
#         # 创建4个独立的 DeiT 模型，分别处理4个麦克风的数据
#         self.deit_models = nn.ModuleList([SingleDeiT() for _ in range(4)])
#         self.embed_dim = self.deit_models[0].embed_dim  # DeiT 的特征维度
#         self.num_heads = 4  # 注意力头的数量
#
#         # 多头注意力机制
#         self.attention = nn.MultiheadAttention(embed_dim=self.embed_dim, num_heads=self.num_heads, batch_first=True)
#
#         # 融合后的全连接层
#         self.fc1 = nn.Linear(self.embed_dim * 4, 512)  # 合并 4 个麦克风的特征
#         self.dropout = nn.Dropout(0.5)
#         self.fc2 = nn.Linear(512, num_classes)  # 最终分类层
#
#     def forward(self, x):
#         # x 是形状为 (batch_size, 4, channels, height, width) 的输入
#         features = [deit(x[:, i, :, :, :]) for i, deit in enumerate(self.deit_models)]  # 提取每个麦克风的特征
#         features = torch.stack(features, dim=1)  # 堆叠特征，形状为 (batch_size, 4, embed_dim)
#
#         # 注意力机制 (query, key, value 都是 features)
#         attended_features, _ = self.attention(features, features, features)  # 输出形状为 (batch_size, 4, embed_dim)
#
#         # 展平并传入全连接层
#         x = attended_features.contiguous().view(attended_features.size(0), -1)  # 或者使用 reshape
#         x = nn.functional.relu(self.fc1(x))
#         x = self.dropout(x)
#         x = self.fc2(x)
#         return x


# 训练和验证完成后，保存结果到 Excel 文件
def save_results_to_excel(file_path, epochs, train_losses, val_losses,
                          train_accuracies, val_accuracies,
                          train_accuracies5, val_accuracies5,
                          train_maes, val_maes):
    # 创建 DataFrame
    data = {
        "Epoch": epochs,
        "Train Loss": train_losses,
        "Val Loss": val_losses,
        "Train Accuracy": train_accuracies,
        "Val Accuracy": val_accuracies,
        "Train threshold5 Accuracy": train_accuracies5,
        "Val threshold5 Accuracy": val_accuracies5,
        "Train MAE": train_maes,
        "Val MAE": val_maes,
    }
    df = pd.DataFrame(data)

    # 保存到 Excel 文件
    df.to_excel(file_path, index=False)
    print(f"Results saved to {file_path}")



# 训练和验证过程
if __name__ == "__main__":
    # 加载数据
    data_dir = './prepared_data'  # 替换为你的数据路径
    dataset = SoundDataset(data_dir, transform=data_transforms['train'])

    # 检查数据集长度
    dataset_length = len(dataset)
    if dataset_length == 0:
        raise ValueError("数据集为空，请检查数据路径或数据格式。")

    # 训练验证集划分
    train_size = int(0.7 * dataset_length)
    val_size = dataset_length - train_size

    # 确保分割大小有效
    if train_size == 0 or val_size == 0:
        raise ValueError("训练集或验证集的大小为零，请检查数据集大小和分割比例。")

    train_dataset, val_dataset = random_split(dataset, [train_size, val_size])

    # 创建数据加载器
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=4, pin_memory=True)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False, num_workers=4, pin_memory=True)

    # 初始化模型、损失函数和优化器
    model = SingleDeiT(num_classes=num_classes)
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=learning_rate)


    # 打印模型参数
    print("Model Parameters:")
    table = []
    for name, param in model.named_parameters():
        table.append([name, param.requires_grad, param.numel()])
    print(tabulate(table, headers=["Layer (type)", "Trainable", "Param #"]))

    # 使用 GPU
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = model.to(device)


    # 训练模型
    train_losses = []
    val_losses = []
    train_accuracies = []
    val_accuracies = []
    train_accuracies5 = []
    val_accuracies5 = []
    train_maes = []
    val_maes = []
    for epoch in range(epochs):
        model.train()
        running_loss = 0.0
        correct = 0
        total = 0
        progress_bar = tqdm(train_loader, desc=f"Epoch {epoch + 1}/{epochs}", leave=False)
        train_mae = 0.0
        train_acc5 = 0.0
        #k = 0
        for inputs, labels in progress_bar:
            inputs, labels = inputs.to(device), labels.to(device)
            optimizer.zero_grad()
            outputs = model(inputs)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            running_loss += loss.item() * inputs.size(0)

            _, preds = torch.max(outputs, 1)
            total += labels.size(0)
            correct += (preds == labels).sum().item()

            MAE, ACC = MAEeval(preds, labels)  # doa evaluation
            # 计算 MAE 和 ACC
            train_mae += MAE
            train_acc5 += ACC
            #k += 1

            # Update progress bar
            progress_bar.set_postfix({'loss': loss.item()})
        # print('train_loader:', len(train_loader))
        # print('k:',k)
        epoch_loss = running_loss / len(train_loader.dataset)
        train_losses.append(epoch_loss)

        train_mae /= len(train_loader)
        train_maes.append(train_mae)

        train_acc = correct / total
        train_accuracies.append(train_acc)

        train_acc5 /= len(train_loader)
        train_accuracies5.append(train_acc5)

        print(f'Epoch {epoch + 1}/{epochs}, Loss: {epoch_loss:.4f}, MAE: {train_mae:.4f}, Accuracy: {train_acc:.4f}, Accuracy(5): {train_acc5:.4f}')

        model.eval()
        val_loss = 0.0
        val_correct = 0
        val_total = 0
        val_mae = 0.0
        val_acc5 = 0.0
        with torch.no_grad():
            for inputs, labels in val_loader:
                inputs, labels = inputs.to(device), labels.to(device)
                outputs = model(inputs)
                loss = criterion(outputs, labels)
                val_loss += loss.item() * inputs.size(0)

                _, preds = torch.max(outputs, 1)
                val_total += labels.size(0)
                val_correct += (preds == labels).sum().item()

                MAE, ACC = MAEeval(preds, labels)
                # 计算 MAE 和 ACC
                val_mae += MAE
                val_acc5 += ACC

        val_loss /= len(val_loader.dataset)
        val_losses.append(val_loss)

        val_mae /= len(val_loader)
        val_maes.append(val_mae)

        val_acc = val_correct / val_total
        val_accuracies.append(val_acc)

        val_acc5 /= len(val_loader)
        val_accuracies5.append(val_acc5)

        print(f'Val Loss: {val_loss:.4f}, Val MAE: {val_mae:.4f}, Val Accuracy: {val_acc:.4f}, Val Accuracy(5): {val_acc5:.4f}')

    # 保存模型
    torch.save(model.state_dict(), 'multi_deit_sound_classification.pth')

    # 保存训练过程中的结果
    save_results_to_excel(
        file_path="DeiT_results.xlsx",
        epochs=list(range(1, len(train_losses) + 1)),
        train_losses=train_losses,
        val_losses=val_losses,
        train_accuracies=train_accuracies,
        val_accuracies=val_accuracies,
        train_accuracies5=train_accuracies5,
        val_accuracies5=val_accuracies5,
        train_maes=train_maes,
        val_maes=val_maes
    )

    # 可视化训练过程
    epochs_range = range(epochs)
    # 可视化训练过程
    plt.figure(figsize=(12, 8))
    plt.subplot(2, 2, 1)
    plt.plot(epochs_range, train_losses, label='Train Loss')
    plt.plot(epochs_range, val_losses, label='Validation Loss')
    plt.legend()
    plt.title('Loss')

    plt.subplot(2, 2, 2)
    plt.plot(epochs_range, train_maes, label='Train MAE')  # 适当保存每轮 MAE
    plt.plot(epochs_range, val_maes, label='Validation MAE')
    plt.legend()
    plt.title('Mean Absolute Error (MAE)')

    plt.subplot(2, 2, 3)
    plt.plot(epochs_range, train_accuracies, label='Train Accuracy')
    plt.plot(epochs_range, val_accuracies, label='Validation Accuracy')
    plt.legend()
    plt.title('Accuracy')

    plt.subplot(2, 2, 4)
    plt.plot(epochs_range, train_accuracies5, label='Train ACC')  # 适当保存每轮 ACC
    plt.plot(epochs_range, val_accuracies5, label='Validation ACC')
    plt.legend()
    plt.title('Accuracy within Threshold')

    plt.tight_layout()
    plt.show()