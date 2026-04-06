import unittest
from quick_sort import quick_sort, quick_sort_inplace


class TestQuickSort(unittest.TestCase):
    """测试快速排序算法"""
    
    def test_quick_sort_normal_case(self):
        """测试正常情况"""
        arr = [64, 34, 25, 12, 22, 11, 90]
        expected = [11, 12, 22, 25, 34, 64, 90]
        result = quick_sort(arr)
        self.assertEqual(result, expected)
    
    def test_quick_sort_empty_list(self):
        """测试空列表"""
        arr = []
        result = quick_sort(arr)
        self.assertEqual(result, [])
    
    def test_quick_sort_single_element(self):
        """测试单个元素"""
        arr = [5]
        result = quick_sort(arr)
        self.assertEqual(result, [5])
    
    def test_quick_sort_duplicates(self):
        """测试重复元素"""
        arr = [5, 2, 9, 1, 5, 6]
        expected = [1, 2, 5, 5, 6, 9]
        result = quick_sort(arr)
        self.assertEqual(result, expected)
    
    def test_quick_sort_already_sorted(self):
        """测试已排序数组"""
        arr = [1, 2, 3, 4, 5]
        expected = [1, 2, 3, 4, 5]
        result = quick_sort(arr)
        self.assertEqual(result, expected)
    
    def test_quick_sort_reverse_sorted(self):
        """测试逆序数组"""
        arr = [5, 4, 3, 2, 1]
        expected = [1, 2, 3, 4, 5]
        result = quick_sort(arr)
        self.assertEqual(result, expected)
    
    def test_quick_sort_all_same(self):
        """测试所有元素相同"""
        arr = [3, 3, 3, 3]
        expected = [3, 3, 3, 3]
        result = quick_sort(arr)
        self.assertEqual(result, expected)
    
    def test_quick_sort_negative_numbers(self):
        """测试负数"""
        arr = [-5, 2, -8, 1, 0]
        expected = [-8, -5, 0, 1, 2]
        result = quick_sort(arr)
        self.assertEqual(result, expected)


class TestQuickSortInplace(unittest.TestCase):
    """测试原地快速排序"""
    
    def test_quick_sort_inplace_normal(self):
        """测试正常情况"""
        arr = [64, 34, 25, 12, 22, 11, 90]
        expected = [11, 12, 22, 25, 34, 64, 90]
        quick_sort_inplace(arr)
        self.assertEqual(arr, expected)
    
    def test_quick_sort_inplace_empty(self):
        """测试空列表"""
        arr = []
        quick_sort_inplace(arr)
        self.assertEqual(arr, [])
    
    def test_quick_sort_inplace_single(self):
        """测试单个元素"""
        arr = [5]
        quick_sort_inplace(arr)
        self.assertEqual(arr, [5])
    
    def test_quick_sort_inplace_duplicates(self):
        """测试重复元素"""
        arr = [5, 2, 9, 1, 5, 6]
        expected = [1, 2, 5, 5, 6, 9]
        quick_sort_inplace(arr)
        self.assertEqual(arr, expected)
    
    def test_quick_sort_inplace_large_list(self):
        """测试较大列表"""
        arr = list(range(100, 0, -1))  # 100到1的逆序
        expected = list(range(1, 101))  # 1到100的顺序
        quick_sort_inplace(arr)
        self.assertEqual(arr, expected)


if __name__ == '__main__':
    unittest.main()