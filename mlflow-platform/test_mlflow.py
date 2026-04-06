"""
MLflow 功能验证脚本
验证实验追踪、模型注册、模型服务三大核心功能
"""

import mlflow
import mlflow.sklearn
from mlflow.tracking import MlflowClient
from sklearn.linear_model import LinearRegression
from sklearn.datasets import make_regression
from sklearn.model_selection import train_test_split
import numpy as np
import os

# 配置 MLflow 追踪服务器
MLFLOW_URI = "http://localhost:5000"
mlflow.set_tracking_uri(MLFLOW_URI)

# 创建 MLflow 客户端
client = MlflowClient(MLFLOW_URI)

print("=" * 60)
print("MLflow 功能验证报告")
print("=" * 60)
print(f"Tracking URI: {MLFLOW_URI}")
print()

# ============================================
# 功能 1: 实验追踪 (Experiment Tracking)
# ============================================
print("【测试 1】实验追踪功能验证")
print("-" * 60)

try:
    # 创建或获取实验
    experiment_name = "MVP-Test-Experiment"
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment:
        experiment_id = experiment.experiment_id
        print(f"✓ 使用已有实验：{experiment_name} (ID: {experiment_id})")
    else:
        experiment_id = client.create_experiment(experiment_name)
        print(f"✓ 创建新实验：{experiment_name} (ID: {experiment_id})")
    
    # 准备测试数据
    X, y = make_regression(n_samples=100, n_features=10, noise=0.1, random_state=42)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # 启动 MLflow 运行
    with mlflow.start_run(experiment_id=experiment_id, run_name="linear-regression-test"):
        # 训练模型
        model = LinearRegression()
        model.fit(X_train, y_train)
        
        # 评估模型
        train_score = model.score(X_train, y_train)
        test_score = model.score(X_test, y_test)
        
        # 记录参数
        mlflow.log_param("n_samples", 100)
        mlflow.log_param("n_features", 10)
        mlflow.log_param("model_type", "LinearRegression")
        
        # 记录指标
        mlflow.log_metric("train_r2", train_score)
        mlflow.log_metric("test_r2", test_score)
        mlflow.log_metric("mse", np.mean((model.predict(X_test) - y_test) ** 2))
        
        # 记录模型
        mlflow.sklearn.log_model(model, "model")
        
        # 获取当前运行信息
        run_id = mlflow.active_run().info.run_id
        print(f"✓ 运行 ID: {run_id}")
        print(f"✓ 训练集 R²: {train_score:.4f}")
        print(f"✓ 测试集 R²: {test_score:.4f}")
        print(f"✓ 模型已记录")
    
    print("✅ 实验追踪功能验证通过\n")
    
except Exception as e:
    print(f"❌ 实验追踪功能验证失败：{str(e)}\n")
    raise

# ============================================
# 功能 2: 模型注册 (Model Registry)
# ============================================
print("【测试 2】模型注册功能验证")
print("-" * 60)

try:
    # 注册模型
    model_name = "MVP-LinearRegression-Model"
    model_uri = f"runs:/{run_id}/model"
    
    # 检查模型是否已注册
    try:
        registered_model = client.get_registered_model(model_name)
        print(f"✓ 模型已存在：{model_name}")
    except:
        # 注册新模型
        model_version = mlflow.register_model(model_uri, model_name)
        print(f"✓ 模型注册成功：{model_name} (Version: {model_version.version})")
    
    # 获取模型版本信息
    versions = client.search_model_versions(f"name='{model_name}'")
    print(f"✓ 当前模型版本数：{len(versions)}")
    
    # 添加模型版本标签
    latest_version = max([int(v.version) for v in versions])
    client.set_model_version_tag(
        name=model_name, 
        version=str(latest_version),
        key="stage", 
        value="production"
    )
    print(f"✓ 模型标签已设置：Version {latest_version} -> Production")
    
    print("✅ 模型注册功能验证通过\n")
    
except Exception as e:
    print(f"❌ 模型注册功能验证失败：{str(e)}\n")
    raise

# ============================================
# 功能 3: 模型加载验证
# ============================================
print("【测试 3】模型加载与服务验证")
print("-" * 60)

try:
    # 从注册表加载模型
    model_uri = f"models:/{model_name}/latest"
    loaded_model = mlflow.sklearn.load_model(model_uri)
    print(f"✓ 模型加载成功：{model_uri}")
    
    # 测试模型预测
    test_prediction = loaded_model.predict(X_test[:5])
    print(f"✓ 模型预测测试成功 (前 5 个样本): {test_prediction[:3]}")
    
    print("✅ 模型服务功能验证通过\n")
    
except Exception as e:
    print(f"❌ 模型服务功能验证失败：{str(e)}\n")
    raise

# ============================================
# 总结报告
# ============================================
print("=" * 60)
print("验证总结")
print("=" * 60)
print(f"实验名称：{experiment_name}")
print(f"运行 ID: {run_id}")
print(f"注册模型：{model_name}")
print(f"模型版本：{len(versions)}")
print()
print("所有核心功能验证通过！✅")
print()
print("下一步:")
print("1. 访问 MLflow UI: http://localhost:5000")
print("2. 查看实验追踪和模型注册")
print("3. 集成 Celery 异步任务队列")
print("=" * 60)
