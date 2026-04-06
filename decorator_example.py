"""
Python 装饰器模式示例
"""
import time
from functools import wraps

# ============ 示例 1: 计时装饰器 ============
def timer_decorator(func):
    """统计函数执行时间的装饰器"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        end = time.time()
        print(f"⏱️  {func.__name__} 耗时：{end - start:.4f}秒")
        return result
    return wrapper

# ============ 示例 2: 日志装饰器 ============
def log_decorator(func):
    """记录函数调用日志的装饰器"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        print(f"📝 调用 {func.__name__}, 参数：args={args}, kwargs={kwargs}")
        result = func(*args, **kwargs)
        print(f"✅ {func.__name__} 返回：{result}")
        return result
    return wrapper

# ============ 示例 3: 重试装饰器 ============
def retry_decorator(times=3):
    """失败时自动重试的装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for i in range(times):
                try:
                    print(f"🔄 第 {i+1} 次尝试")
                    return func(*args, **kwargs)
                except Exception as e:
                    print(f"❌ 失败：{e}")
                    if i == times - 1:
                        raise
        return wrapper
    return decorator

# ============ 使用装饰器 ============

@timer_decorator
def calculate_sum(n):
    """计算 1 到 n 的和"""
    total = 0
    for i in range(n):
        total += i
    return total

@log_decorator
def greet(name, greeting="你好"):
    """打招呼函数"""
    return f"{greeting}, {name}!"

@retry_decorator(times=3)
def risky_operation():
    """可能失败的操作"""
    import random
    if random.random() < 0.7:
        raise RuntimeError("随机失败")
    return "成功!"

# ============ 测试 ============
if __name__ == "__main__":
    print("=" * 50)
    print("示例 1: 计时装饰器")
    print("=" * 50)
    result = calculate_sum(1000000)
    print(f"结果：{result}\n")
    
    print("=" * 50)
    print("示例 2: 日志装饰器")
    print("=" * 50)
    message = greet("Zacon", "早上好")
    print(f"最终消息：{message}\n")
    
    print("=" * 50)
    print("示例 3: 重试装饰器")
    print("=" * 50)
    try:
        outcome = risky_operation()
        print(f"最终结果：{outcome}")
    except RuntimeError as e:
        print(f"最终失败：{e}")
