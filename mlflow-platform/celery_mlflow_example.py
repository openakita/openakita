"""
MLflow + Celery 集成示例
实现异步模型训练和实验追踪
"""

from celery import Celery
import mlflow
import mlflow.sklearn
from sklearn.linear_model import LinearRegression
from sklearn.ensemble import RandomForestRegressor
from sklearn.datasets import make_regression
from sklearn.model_selection import train_test_split
import time
import json

# Celery 配置
celery_app = Celery(
    'mlflow_tasks',
    broker='redis://localhost:6379/0',
    backend='redis://localhost:6379/1'
)

# MLflow 配置
MLFLOW_URI = "http://localhost:5000"
mlflow.set_tracking_uri(MLFLOW_URI)

# ============================================
# 任务 1: 异步模型训练
# ============================================
@celery_app.task(bind=True)
def train_model_async(self, experiment_name, model_type='linear', n_samples=100, n_features=10):
    """
    异步训练模型并记录到 MLflow
    
    Args:
        experiment_name: 实验名称
        model_type: 模型类型 ('linear' 或 'random_forest')
        n_samples: 样本数量
        n_features: 特征数量
    
    Returns:
        dict: 训练结果摘要
    """
    try:
        # 更新任务状态
        self.update_state(state='PROGRESS', meta={'status': '准备数据'})
        
        # 准备数据
        X, y = make_regression(n_samples=n_samples, n_features=n_features, noise=0.1, random_state=42)
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        
        self.update_state(state='PROGRESS', meta={'status': f'训练 {model_type} 模型'})
        
        # 选择模型
        if model_type == 'linear':
            model = LinearRegression()
        elif model_type == 'random_forest':
            model = RandomForestRegressor(n_estimators=10, random_state=42)
        else:
            raise ValueError(f"不支持的模型类型：{model_type}")
        
        # 启动 MLflow 运行
        with mlflow.start_run(experiment_name=experiment_name, run_name=f"{model_type}-async-run"):
            # 训练模型
            model.fit(X_train, y_train)
            
            # 评估模型
            train_score = model.score(X_train, y_train)
            test_score = model.score(X_test, y_test)
            
            # 记录参数
            mlflow.log_param("model_type", model_type)
            mlflow.log_param("n_samples", n_samples)
            mlflow.log_param("n_features", n_features)
            mlflow.log_param("timestamp", time.strftime("%Y-%m-%d %H:%M:%S"))
            
            # 记录指标
            mlflow.log_metric("train_r2", train_score)
            mlflow.log_metric("test_r2", test_score)
            mlflow.log_metric("mse", float(np.mean((model.predict(X_test) - y_test) ** 2)))
            
            # 记录模型
            mlflow.sklearn.log_model(model, "model")
            
            run_id = mlflow.active_run().info.run_id
            
            self.update_state(state='PROGRESS', meta={'status': '记录完成'})
            
            result = {
                'status': 'SUCCESS',
                'run_id': run_id,
                'experiment_name': experiment_name,
                'model_type': model_type,
                'train_r2': float(train_score),
                'test_r2': float(test_score),
                'mlflow_url': f"{MLFLOW_URI}/#/experiments/0/runs/{run_id}"
            }
            
            return result
    
    except Exception as e:
        return {
            'status': 'FAILED',
            'error': str(e)
        }

# ============================================
# 任务 2: 批量实验
# ============================================
@celery_app.task
def run_batch_experiments(experiment_name, model_configs):
    """
    批量运行多个实验配置
    
    Args:
        experiment_name: 实验名称前缀
        model_configs: 模型配置列表
    
    Returns:
        list: 所有实验结果
    """
    results = []
    
    for config in model_configs:
        task = train_model_async.delay(
            experiment_name=f"{experiment_name}-{config['model_type']}",
            model_type=config['model_type'],
            n_samples=config.get('n_samples', 100),
            n_features=config.get('n_features', 10)
        )
        results.append({
            'config': config,
            'task_id': task.id
        })
    
    return results

# ============================================
# 使用示例
# ============================================
if __name__ == '__main__':
    import numpy as np
    
    print("=" * 60)
    print("MLflow + Celery 集成示例")
    print("=" * 60)
    
    # 示例 1: 单个异步训练任务
    print("\n【示例 1】提交单个训练任务")
    task = train_model_async.delay(
        experiment_name="Celery-Test-Experiment",
        model_type="linear",
        n_samples=200,
        n_features=20
    )
    print(f"任务 ID: {task.id}")
    print("等待任务完成...")
    result = task.get(timeout=60)
    print(f"任务状态：{result['status']}")
    if result['status'] == 'SUCCESS':
        print(f"运行 ID: {result['run_id']}")
        print(f"测试集 R²: {result['test_r2']:.4f}")
        print(f"MLflow URL: {result['mlflow_url']}")
    
    # 示例 2: 批量实验
    print("\n【示例 2】批量实验")
    configs = [
        {'model_type': 'linear', 'n_samples': 100, 'n_features': 10},
        {'model_type': 'random_forest', 'n_samples': 100, 'n_features': 10},
        {'model_type': 'linear', 'n_samples': 200, 'n_features': 20},
        {'model_type': 'random_forest', 'n_samples': 200, 'n_features': 20},
    ]
    
    batch_task = run_batch_experiments.delay("Batch-Experiment", configs)
    batch_results = batch_task.get(timeout=120)
    
    print(f"提交任务数：{len(batch_results)}")
    for i, res in enumerate(batch_results):
        print(f"  任务 {i+1}: {res['config']['model_type']} (Task ID: {res['task_id']})")
    
    print("\n✅ 所有示例执行完成！")
    print(f"访问 MLflow UI: {MLFLOW_URI}")
