import pandas as pd

# ================================
# Pandas CSV 文件基本统计分析示例
# ================================

# 1. 加载 CSV 文件
# 将 'your_file.csv' 替换为你的实际文件路径
df = pd.read_csv('your_file.csv')

# 2. 查看基本信息
print("=== 数据基本信息 ===")
print(f"数据形状：{df.shape}")  # (行数，列数)
print(f"\n列名：{df.columns.tolist()}")
print(f"\n数据类型：\n{df.dtypes}")
print(f"\n缺失值统计：\n{df.isnull().sum()}")

# 3. 描述性统计（核心功能）
print("\n=== 描述性统计 ===")
print(df.describe())

# 4. 数值列的详细统计
print("\n=== 数值列详细统计 ===")
numeric_cols = df.select_dtypes(include=['number']).columns

for col in numeric_cols:
    print(f"\n{col}:")
    print(f"  均值：{df[col].mean():.2f}")
    print(f"  中位数：{df[col].median():.2f}")
    print(f"  标准差：{df[col].std():.2f}")
    print(f"  最小值：{df[col].min()}")
    print(f"  最大值：{df[col].max()}")
    print(f"  25% 分位数：{df[col].quantile(0.25):.2f}")
    print(f"  50% 分位数（中位数）：{df[col].quantile(0.50):.2f}")
    print(f"  75% 分位数：{df[col].quantile(0.75):.2f}")

# 5. 相关系数矩阵（数值列之间）
print("\n=== 相关系数矩阵 ===")
print(df.corr())

# 6. 可选：保存统计结果到文件
# df.describe().to_excel('statistics_summary.xlsx')
# df.corr().to_csv('correlation_matrix.csv')
