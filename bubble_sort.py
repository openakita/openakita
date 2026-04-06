"""
冒泡排序算法实现 (Bubble Sort Implementation)

冒泡排序是一种简单的排序算法，通过重复遍历待排序序列，
比较相邻元素并在顺序错误时交换它们，使较大的元素逐渐"冒泡"到序列末尾。

时间复杂度:
- 最坏情况: O(n²)
- 最好情况: O(n) - 当序列已排序时（优化版本）
- 平均情况: O(n²)

空间复杂度: O(1) - 原地排序

稳定性: 稳定排序（相等元素的相对位置不会改变）
"""


def bubble_sort(arr):
    """
    冒泡排序基础版本
    
    参数:
        arr: 待排序的列表（原地修改）
    
    返回:
        排序后的列表
    """
    n = len(arr)
    
    # 外层循环控制排序轮数，n 个元素需要 n-1 轮
    for i in range(n - 1):
        # 内层循环进行相邻元素比较和交换
        # 每轮结束后，最大的元素会"冒泡"到末尾，所以减去 i
        for j in range(n - 1 - i):
            # 如果前一个元素大于后一个元素，则交换
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
    
    return arr


def bubble_sort_optimized(arr):
    """
    冒泡排序优化版本
    
    优化点：添加标志位检测某轮是否发生交换
    如果某轮没有发生任何交换，说明序列已经有序，可以提前结束
    
    参数:
        arr: 待排序的列表（原地修改）
    
    返回:
        排序后的列表
    """
    n = len(arr)
    
    for i in range(n - 1):
        # 标志位：记录本轮是否发生交换
        swapped = False
        
        for j in range(n - 1 - i):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
                swapped = True
        
        # 如果本轮没有交换，说明已经有序，提前结束
        if not swapped:
            break
    
    return arr


def bubble_sort_verbose(arr):
    """
    冒泡排序详细演示版本（用于教学）
    
    打印每一轮的排序过程，帮助理解算法工作原理
    
    参数:
        arr: 待排序的列表
    
    返回:
        排序后的列表
    """
    n = len(arr)
    print(f"初始数组：{arr}")
    print(f"数组长度：{n}\n")
    
    for i in range(n - 1):
        print(f"第 {i + 1} 轮排序:")
        swapped = False
        
        for j in range(n - 1 - i):
            print(f"  比较位置 {j} 和 {j + 1}: {arr[j]} vs {arr[j + 1]}", end="")
            
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
                swapped = True
                print(f" → 交换 → {arr}")
            else:
                print(" → 不交换")
        
        print(f"  第 {i + 1} 轮结果：{arr}\n")
        
        if not swapped:
            print("  本轮无交换，排序提前结束！\n")
            break
    
    return arr


# ==================== 测试用例 ====================

def test_bubble_sort():
    """测试冒泡排序的各种场景"""
    
    print("=" * 60)
    print("冒泡排序测试用例")
    print("=" * 60)
    
    # 测试用例 1: 普通乱序数组
    print("\n【测试 1】普通乱序数组")
    arr1 = [64, 34, 25, 12, 22, 11, 90]
    print(f"排序前：{arr1}")
    result1 = bubble_sort(arr1.copy())
    print(f"排序后：{result1}")
    assert result1 == [11, 12, 22, 25, 34, 64, 90], "测试 1 失败"
    print("✓ 测试 1 通过")
    
    # 测试用例 2: 已排序数组（测试优化版本）
    print("\n【测试 2】已排序数组（测试优化版本）")
    arr2 = [1, 2, 3, 4, 5]
    print(f"排序前：{arr2}")
    result2 = bubble_sort_optimized(arr2.copy())
    print(f"排序后：{result2}")
    assert result2 == [1, 2, 3, 4, 5], "测试 2 失败"
    print("✓ 测试 2 通过")
    
    # 测试用例 3: 逆序数组（最坏情况）
    print("\n【测试 3】逆序数组（最坏情况）")
    arr3 = [5, 4, 3, 2, 1]
    print(f"排序前：{arr3}")
    result3 = bubble_sort(arr3.copy())
    print(f"排序后：{result3}")
    assert result3 == [1, 2, 3, 4, 5], "测试 3 失败"
    print("✓ 测试 3 通过")
    
    # 测试用例 4: 包含重复元素
    print("\n【测试 4】包含重复元素")
    arr4 = [3, 1, 4, 1, 5, 9, 2, 6, 5]
    print(f"排序前：{arr4}")
    result4 = bubble_sort(arr4.copy())
    print(f"排序后：{result4}")
    assert result4 == [1, 1, 2, 3, 4, 5, 5, 6, 9], "测试 4 失败"
    print("✓ 测试 4 通过")
    
    # 测试用例 5: 单元素数组
    print("\n【测试 5】单元素数组")
    arr5 = [42]
    print(f"排序前：{arr5}")
    result5 = bubble_sort(arr5.copy())
    print(f"排序后：{result5}")
    assert result5 == [42], "测试 5 失败"
    print("✓ 测试 5 通过")
    
    # 测试用例 6: 空数组
    print("\n【测试 6】空数组")
    arr6 = []
    print(f"排序前：{arr6}")
    result6 = bubble_sort(arr6.copy())
    print(f"排序后：{result6}")
    assert result6 == [], "测试 6 失败"
    print("✓ 测试 6 通过")
    
    # 测试用例 7: 负数数组
    print("\n【测试 7】包含负数")
    arr7 = [-5, 3, -1, 0, 8, -10, 2]
    print(f"排序前：{arr7}")
    result7 = bubble_sort(arr7.copy())
    print(f"排序后：{result7}")
    assert result7 == [-10, -5, -1, 0, 2, 3, 8], "测试 7 失败"
    print("✓ 测试 7 通过")
    
    print("\n" + "=" * 60)
    print("所有测试通过！✓")
    print("=" * 60)


def demo_verbose_sort():
    """演示详细排序过程"""
    print("\n" + "=" * 60)
    print("冒泡排序详细演示")
    print("=" * 60 + "\n")
    
    arr = [5, 2, 8, 1, 9]
    bubble_sort_verbose(arr)


if __name__ == "__main__":
    # 运行所有测试
    test_bubble_sort()
    
    # 可选：运行详细演示（取消注释查看）
    # demo_verbose_sort()
    
    print("\n💡 使用示例:")
    print("""
# 基础版本
arr = [64, 34, 25, 12, 22, 11, 90]
bubble_sort(arr)
print(arr)  # [11, 12, 22, 25, 34, 64, 90]

# 优化版本（推荐）
arr = [64, 34, 25, 12, 22, 11, 90]
bubble_sort_optimized(arr)
print(arr)  # [11, 12, 22, 25, 34, 64, 90]

# 详细演示版本（教学用）
arr = [5, 2, 8, 1, 9]
bubble_sort_verbose(arr)
    """)
