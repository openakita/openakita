"""
快速排序算法实现
时间复杂度：平均 O(n log n)，最坏 O(n²)
空间复杂度：O(log n)
"""


def quick_sort(arr):
    """
    快速排序主函数
    
    参数:
        arr: 待排序的列表
    
    返回:
        排序后的新列表
    """
    # 基准情况：空列表或单元素列表已有序
    if len(arr) <= 1:
        return arr
    
    # 选择中间元素作为枢轴（避免最坏情况）
    pivot = arr[len(arr) // 2]
    
    # 分区：小于、等于、大于枢轴的元素
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    
    # 递归排序左右分区并合并
    return quick_sort(left) + middle + quick_sort(right)


def quick_sort_inplace(arr, low=0, high=None):
    """
    原地快速排序（节省空间）
    
    参数:
        arr: 待排序的列表（直接修改原列表）
        low: 起始索引
        high: 结束索引
    """
    if high is None:
        high = len(arr) - 1
    
    if low < high:
        # 分区操作，获取枢轴位置
        pivot_index = partition(arr, low, high)
        
        # 递归排序左右两部分
        quick_sort_inplace(arr, low, pivot_index - 1)
        quick_sort_inplace(arr, pivot_index + 1, high)


def partition(arr, low, high):
    """
    分区函数：将数组分为小于和大于枢轴的两部分
    
    参数:
        arr: 待分区列表
        low: 起始索引
        high: 结束索引
    
    返回:
        枢轴的最终位置
    """
    # 选择最右元素作为枢轴
    pivot = arr[high]
    i = low - 1  # i 指向小于 pivot 区域的最后一个元素
    
    for j in range(low, high):
        if arr[j] <= pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]  # 交换
    
    # 将枢轴放到正确位置
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1


# 测试代码
if __name__ == "__main__":
    # 测试用例
    test_cases = [
        [64, 34, 25, 12, 22, 11, 90],
        [5, 2, 9, 1, 5, 6],
        [1],
        [],
        [3, 3, 3, 3],
        [9, 7, 5, 3, 1],
    ]
    
    print("快速排序测试")
    print("=" * 40)
    
    for i, test in enumerate(test_cases, 1):
        original = test.copy()
        result = quick_sort(test)
        print(f"测试 {i}: {original} → {result}")
    
    print("\n原地排序测试")
    print("=" * 40)
    arr = [64, 34, 25, 12, 22, 11, 90]
    print(f"排序前：{arr}")
    quick_sort_inplace(arr)
    print(f"排序后：{arr}")
