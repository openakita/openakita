"""直接调用 org_freeze_node 工具"""
import sys
import asyncio
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from src.openakita.orgs.manager import OrgManager
from src.openakita.orgs.runtime import OrgRuntime

async def main():
    # 使用当前工作区的数据目录
    data_dir = Path(__file__).parent / "data"
    print(f"数据目录：{data_dir}")
    
    # 创建组织管理器
    manager = OrgManager(data_dir)
    
    # 列出所有组织
    orgs = manager.list_orgs()
    print(f"可用组织：{orgs}")
    
    if not orgs:
        print("错误：没有找到任何组织")
        return
    
    org_id = orgs[0]["id"]
    print(f"使用组织：{org_id}")
    
    # 创建运行时
    runtime = OrgRuntime(manager)
    
    # 获取工具处理器
    handler = runtime.get_tool_handler(org_id)
    if not handler:
        print("错误：无法获取工具处理器")
        return
    
    # 调用 org_freeze_node
    print("\n正在冻结 dev 节点...")
    result = await handler.handle(
        tool_name="org_freeze_node",
        arguments={
            "node_id": "dev",
            "reason": "代码审查未通过，暂停工作权限"
        },
        org_id=org_id,
        node_id="CEO"
    )
    
    print(f"结果：{result}")

if __name__ == "__main__":
    asyncio.run(main())
