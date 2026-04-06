"""
斐波那契数列计算器 - 支持大数

Python 原生支持任意精度整数，无需特殊处理即可计算超大斐波那契数。
使用迭代法实现，时间复杂度 O(n)，空间复杂度 O(1)。
"""


def fibonacci(n: int) -> int:
    """
    计算第 n 个斐波那契数（支持大数）
    
    Args:
        n: 斐波那契数的索引（从 0 开始）
           F(0) = 0, F(1) = 1, F(2) = 1, F(3) = 2, ...
    
    Returns:
        第 n 个斐波那契数
    
    Raises:
        ValueError: 当 n 为负数时
        TypeError: 当 n 不是整数时
    
    Examples:
        >>> fibonacci(0)
        0
        >>> fibonacci(1)
        1
        >>> fibonacci(10)
        55
        >>> fibonacci(50)
        12586269025
        >>> fibonacci(100)
        354224848179261915075
    """
    if not isinstance(n, int):
        raise TypeError("n 必须是整数")
    if n < 0:
        raise ValueError("n 必须是非负整数")
    
    if n == 0:
        return 0
    if n == 1:
        return 1
    
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    
    return b


def fibonacci_sequence(n: int) -> list:
    """
    生成前 n 个斐波那契数的列表
    
    Args:
        n: 要生成的斐波那契数个数
    
    Returns:
        包含前 n 个斐波那契数的列表
    
    Examples:
        >>> fibonacci_sequence(10)
        [0, 1, 1, 2, 3, 5, 8, 13, 21, 34]
    """
    if n <= 0:
        return []
    if n == 1:
        return [0]
    
    seq = [0, 1]
    for i in range(2, n):
        seq.append(seq[i-1] + seq[i-2])
    
    return seq


def fibonacci_with_memoization(n: int, memo: dict = None) -> int:
    """
    使用记忆化递归计算斐波那契数（适合重复调用）
    
    Args:
        n: 斐波那契数的索引
        memo: 缓存字典（可选）
    
    Returns:
        第 n 个斐波那契数
    """
    if memo is None:
        memo = {}
    
    if n in memo:
        return memo[n]
    if n == 0:
        return 0
    if n == 1:
        return 1
    
    memo[n] = fibonacci_with_memoization(n - 1, memo) + fibonacci_with_memoization(n - 2, memo)
    return memo[n]


if __name__ == "__main__":
    # 使用示例
    print("=" * 60)
    print("斐波那契数列计算器 - 大数支持")
    print("=" * 60)
    
    # 示例 1: 计算小数值
    print("\n【示例 1】计算前 20 个斐波那契数:")
    for i in range(20):
        print(f"F({i:2d}) = {fibonacci(i)}")
    
    # 示例 2: 计算大数值
    print("\n【示例 2】计算超大斐波那契数:")
    test_cases = [100, 500, 1000, 5000]
    for n in test_cases:
        result = fibonacci(n)
        print(f"\nF({n}) = {result}")
        print(f"  位数：{len(str(result))} 位")
    
    # 示例 3: 生成数列
    print("\n【示例 3】生成前 15 个斐波那契数:")
    print(fibonacci_sequence(15))
    
    # 示例 4: 性能测试
    print("\n【示例 4】性能测试 - 计算 F(10000):")
    import time
    start = time.time()
    result = fibonacci(10000)
    elapsed = time.time() - start
    print(f"  计算时间：{elapsed:.4f} 秒")
    print(f"  结果位数：{len(str(result))} 位")
    print(f"  结果前 50 位：{str(result)[:50]}...")
