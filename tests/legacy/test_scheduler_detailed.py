"""
定时任务调度器详细测试

全面测试 Scheduler 模块的各种场景
"""

import asyncio
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


async def test_once_task():
    """测试一次性任务"""
    print("\n1. 测试一次性任务 (Once)")
    print("-" * 40)
    
    from openakita.scheduler import TaskScheduler, ScheduledTask
    
    with tempfile.TemporaryDirectory() as tmpdir:
        executed_tasks = []
        
        async def executor(task):
            executed_tasks.append(task.id)
            print(f"      执行任务: {task.name}")
            return True, f"完成: {task.name}"
        
        scheduler = TaskScheduler(
            storage_path=Path(tmpdir),
            executor=executor,
            check_interval_seconds=1,
        )
        await scheduler.start()
        
        try:
            # 创建一个 2 秒后执行的任务
            task = ScheduledTask.create_once(
                name="2秒后执行的任务",
                description="测试一次性任务",
                run_at=datetime.now() + timedelta(seconds=2),
                prompt="执行一次性操作",
            )
            
            task_id = await scheduler.add_task(task)
            print(f"   ✅ 创建任务: {task_id}")
            print(f"      计划执行时间: {task.next_run}")
            
            # 等待任务执行
            print("      等待任务执行...")
            await asyncio.sleep(4)
            
            # 检查是否执行
            if task_id in executed_tasks:
                print("   ✅ 任务成功执行!")
                
                # 检查任务状态
                task = scheduler.get_task(task_id)
                print(f"      任务状态: {task.status.value}")
                print(f"      执行次数: {task.run_count}")
                return True
            else:
                print("   ❌ 任务未执行")
                return False
                
        finally:
            await scheduler.stop()


async def test_interval_task():
    """测试间隔任务"""
    print("\n2. 测试间隔任务 (Interval)")
    print("-" * 40)
    
    from openakita.scheduler import TaskScheduler, ScheduledTask
    from openakita.scheduler.triggers import IntervalTrigger
    
    with tempfile.TemporaryDirectory() as tmpdir:
        execution_count = {"count": 0}
        
        async def executor(task):
            execution_count["count"] += 1
            print(f"      第 {execution_count['count']} 次执行: {task.name}")
            return True, f"执行 #{execution_count['count']}"
        
        scheduler = TaskScheduler(
            storage_path=Path(tmpdir),
            executor=executor,
            check_interval_seconds=1,
        )
        await scheduler.start()
        
        try:
            # 创建间隔任务 (使用最小间隔 1 分钟，但我们会手动修改)
            task = ScheduledTask.create_interval(
                name="短间隔任务",
                description="测试间隔任务",
                interval_minutes=1,
                prompt="间隔执行",
            )
            
            # 为了测试，手动创建一个 2 秒间隔的触发器
            trigger = IntervalTrigger(interval_minutes=1)
            trigger.interval = timedelta(seconds=2)
            
            task.next_run = datetime.now()
            
            task_id = await scheduler.add_task(task)
            scheduler._triggers[task_id] = trigger
            
            print(f"   ✅ 创建任务: {task_id}")
            
            # 等待多次执行
            print("      等待 6 秒观察执行...")
            await asyncio.sleep(6)
            
            print(f"   ✅ 执行次数: {execution_count['count']}")
            
            if execution_count["count"] >= 2:
                print("   ✅ 间隔任务工作正常!")
                return True
            else:
                print("   ⚠️ 执行次数少于预期")
                return False
                
        finally:
            await scheduler.stop()


async def test_cron_expressions():
    """测试 Cron 表达式解析"""
    print("\n3. 测试 Cron 表达式")
    print("-" * 40)
    
    from openakita.scheduler import CronTrigger
    
    test_cases = [
        ("* * * * *", "每分钟"),
        ("0 * * * *", "每小时整点"),
        ("0 0 * * *", "每天午夜"),
        ("0 9 * * *", "每天上午9点"),
        ("0 9 * * 1", "每周一上午9点"),
        ("0 0 1 * *", "每月1日午夜"),
        ("*/5 * * * *", "每5分钟"),
        ("0 9-18 * * *", "每天9-18点整点"),
        ("0 9,12,18 * * *", "每天9点、12点、18点"),
        ("30 8 * * 1-5", "工作日8:30"),
    ]
    
    all_passed = True
    
    for expr, desc in test_cases:
        try:
            trigger = CronTrigger(expr)
            next_run = trigger.get_next_run_time()
            print(f"   ✅ '{expr}' ({desc})")
            print(f"      下次执行: {next_run.strftime('%Y-%m-%d %H:%M')}")
        except Exception as e:
            print(f"   ❌ '{expr}' ({desc}): {e}")
            all_passed = False
    
    return all_passed


async def test_task_persistence():
    """测试任务持久化"""
    print("\n4. 测试任务持久化")
    print("-" * 40)
    
    from openakita.scheduler import TaskScheduler, ScheduledTask
    
    with tempfile.TemporaryDirectory() as tmpdir:
        storage_path = Path(tmpdir)
        
        # 第一阶段：创建任务
        scheduler1 = TaskScheduler(storage_path=storage_path)
        await scheduler1.start()
        
        task = ScheduledTask.create_cron(
            name="持久化测试任务",
            description="测试持久化功能",
            cron_expression="0 9 * * *",
            prompt="每日任务",
            user_id="test_user",
        )
        
        task_id = await scheduler1.add_task(task)
        print(f"   ✅ 创建任务: {task_id}")
        
        await scheduler1.stop()
        print("   ✅ 调度器已停止")
        
        # 第二阶段：重新加载
        scheduler2 = TaskScheduler(storage_path=storage_path)
        await scheduler2.start()
        
        loaded_task = scheduler2.get_task(task_id)
        
        if loaded_task:
            print(f"   ✅ 任务成功加载!")
            print(f"      名称: {loaded_task.name}")
            print(f"      触发器: {loaded_task.trigger_type.value}")
            print(f"      用户: {loaded_task.user_id}")
            
            await scheduler2.stop()
            return True
        else:
            print("   ❌ 任务加载失败")
            await scheduler2.stop()
            return False


async def test_task_lifecycle():
    """测试任务生命周期"""
    print("\n5. 测试任务生命周期")
    print("-" * 40)
    
    from openakita.scheduler import ScheduledTask, TaskStatus, TriggerType
    
    # 创建任务
    task = ScheduledTask.create_once(
        name="生命周期测试",
        description="测试",
        run_at=datetime.now() + timedelta(hours=1),
        prompt="执行",
    )
    
    print(f"   初始状态: {task.status.value}")
    assert task.status == TaskStatus.PENDING
    
    # 标记运行中
    task.mark_running()
    print(f"   运行中: {task.status.value}")
    assert task.status == TaskStatus.RUNNING
    
    # 标记完成
    task.mark_completed()
    print(f"   完成后: {task.status.value}")
    assert task.status == TaskStatus.COMPLETED
    assert task.run_count == 1
    
    # 对于非一次性任务
    task2 = ScheduledTask.create_cron(
        name="周期任务",
        description="测试",
        cron_expression="0 9 * * *",
        prompt="执行",
    )
    
    task2.mark_running()
    task2.mark_completed(next_run=datetime.now() + timedelta(days=1))
    print(f"   Cron 任务完成后: {task2.status.value}")
    assert task2.status == TaskStatus.SCHEDULED  # 不是 COMPLETED
    
    # 禁用
    task2.disable()
    print(f"   禁用后: {task2.status.value}")
    assert task2.status == TaskStatus.DISABLED
    
    # 重新启用
    task2.enable()
    print(f"   启用后: {task2.status.value}")
    assert task2.status == TaskStatus.SCHEDULED
    
    print("   ✅ 所有生命周期测试通过")
    return True


async def test_concurrent_tasks():
    """测试并发任务执行"""
    print("\n6. 测试并发任务执行")
    print("-" * 40)
    
    from openakita.scheduler import TaskScheduler, ScheduledTask
    
    with tempfile.TemporaryDirectory() as tmpdir:
        execution_log = []
        
        async def slow_executor(task):
            execution_log.append((task.name, "start", datetime.now()))
            print(f"      开始执行: {task.name}")
            await asyncio.sleep(2)  # 模拟耗时操作
            execution_log.append((task.name, "end", datetime.now()))
            print(f"      完成执行: {task.name}")
            return True, f"完成: {task.name}"
        
        scheduler = TaskScheduler(
            storage_path=Path(tmpdir),
            executor=slow_executor,
            max_concurrent=3,  # 最多同时 3 个
            check_interval_seconds=1,
        )
        await scheduler.start()
        
        try:
            # 创建 5 个立即执行的任务
            task_ids = []
            for i in range(5):
                task = ScheduledTask.create_once(
                    name=f"并发任务_{i+1}",
                    description="测试并发",
                    run_at=datetime.now(),
                    prompt=f"任务 {i+1}",
                )
                task_id = await scheduler.add_task(task)
                task_ids.append(task_id)
            
            print(f"   ✅ 创建了 {len(task_ids)} 个任务")
            
            # 等待执行
            print("      等待执行 (max_concurrent=3)...")
            await asyncio.sleep(8)
            
            # 分析执行日志
            starts = [e for e in execution_log if e[1] == "start"]
            ends = [e for e in execution_log if e[1] == "end"]
            
            print(f"   ✅ 开始执行: {len(starts)} 次")
            print(f"   ✅ 完成执行: {len(ends)} 次")
            
            return len(ends) >= 5  # 所有任务都应该执行
            
        finally:
            await scheduler.stop()


async def test_task_failure():
    """测试任务失败处理"""
    print("\n7. 测试任务失败处理")
    print("-" * 40)
    
    from openakita.scheduler import TaskScheduler, ScheduledTask
    
    with tempfile.TemporaryDirectory() as tmpdir:
        async def failing_executor(task):
            raise RuntimeError("模拟执行失败")
        
        scheduler = TaskScheduler(
            storage_path=Path(tmpdir),
            executor=failing_executor,
            check_interval_seconds=1,
        )
        await scheduler.start()
        
        try:
            task = ScheduledTask.create_once(
                name="会失败的任务",
                description="测试失败处理",
                run_at=datetime.now(),
                prompt="必定失败",
            )
            
            task_id = await scheduler.add_task(task)
            print(f"   ✅ 创建任务: {task_id}")
            
            # 等待执行
            await asyncio.sleep(3)
            
            # 检查状态
            task = scheduler.get_task(task_id)
            print(f"   任务状态: {task.status.value}")
            print(f"   失败次数: {task.fail_count}")
            
            if task.fail_count > 0:
                print("   ✅ 失败计数正确")
                return True
            else:
                print("   ❌ 失败未被记录")
                return False
                
        finally:
            await scheduler.stop()


async def test_manual_trigger():
    """测试手动触发"""
    print("\n8. 测试手动触发")
    print("-" * 40)
    
    from openakita.scheduler import TaskScheduler, ScheduledTask
    
    with tempfile.TemporaryDirectory() as tmpdir:
        executed = {"count": 0}
        
        async def executor(task):
            executed["count"] += 1
            return True, f"手动触发执行 #{executed['count']}"
        
        scheduler = TaskScheduler(
            storage_path=Path(tmpdir),
            executor=executor,
        )
        await scheduler.start()
        
        try:
            # 创建一个很久之后才执行的任务
            task = ScheduledTask.create_once(
                name="远期任务",
                description="测试手动触发",
                run_at=datetime.now() + timedelta(days=365),
                prompt="本来很久以后才执行",
            )
            
            task_id = await scheduler.add_task(task)
            print(f"   ✅ 创建任务: {task_id}")
            print(f"      计划时间: {task.next_run} (1年后)")
            
            # 手动触发
            print("      手动触发执行...")
            execution = await scheduler.trigger_now(task_id)
            
            if execution and executed["count"] > 0:
                print(f"   ✅ 手动触发成功!")
                print(f"      执行状态: {execution.status}")
                print(f"      执行结果: {execution.result}")
                return True
            else:
                print("   ❌ 手动触发失败")
                return False
                
        finally:
            await scheduler.stop()


async def main():
    """主测试流程"""
    print("\n" + "=" * 60)
    print("定时任务调度器详细测试")
    print("=" * 60)
    
    results = {"passed": 0, "failed": 0}
    
    tests = [
        ("一次性任务", test_once_task),
        ("间隔任务", test_interval_task),
        ("Cron 表达式", test_cron_expressions),
        ("任务持久化", test_task_persistence),
        ("任务生命周期", test_task_lifecycle),
        ("并发任务执行", test_concurrent_tasks),
        ("任务失败处理", test_task_failure),
        ("手动触发", test_manual_trigger),
    ]
    
    for name, test_func in tests:
        try:
            success = await test_func()
            if success:
                results["passed"] += 1
            else:
                results["failed"] += 1
        except Exception as e:
            print(f"   ❌ 异常: {e}")
            results["failed"] += 1
    
    # 汇总
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    print(f"  ✅ 通过: {results['passed']}")
    print(f"  ❌ 失败: {results['failed']}")
    print("=" * 60)
    
    return results["failed"] == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)

