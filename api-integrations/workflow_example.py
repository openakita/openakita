"""
MVP API 集成综合示例 - 自动化工作流
演示如何组合使用多个 API 实现完整的业务流程

场景：项目任务完成后的自动化通知流程
1. 更新数据库任务状态
2. 生成项目报告文档
3. 导出进度数据到表格
4. 发送通知消息到多个渠道
5. 记录日志和性能指标
"""

from datetime import datetime
from typing import Dict, List


# ============================================================================
# 步骤 1: 导入所有 API 模块
# ============================================================================

# 数据库 API
from api-integrations.03_database_api import PostgreSQLAPI

# 文档 API
from api-integrations.07_document_api import GoogleDocsAPI

# 表格 API
from api-integrations.08_spreadsheet_api import GoogleSheetsAPI, ExcelAPI

# 消息推送 API
from api-integrations.05_message_api import DingTalkAPI, WeComAPI, FeishuAPI

# 邮件 API
from api-integrations.01_email_api import EmailAPI

# 日志监控 API
from api-integrations.10_logging_api import StructuredLogger, PerformanceMonitor

# 身份验证 API
from api-integrations.09_auth_api import JWTAuthAPI

# 文件存储 API
from api-integrations.04_storage_api import LocalStorageAPI


# ============================================================================
# 步骤 2: 初始化所有服务
# ============================================================================

def initialize_services():
    """初始化所有 API 服务"""
    
    # 日志系统
    logger = StructuredLogger(
        name="workflow_automation",
        level=10,  # DEBUG
        json_format=True,
        output_file="workflow.log"
    )
    
    # 性能监控
    monitor = PerformanceMonitor(logger)
    
    # 数据库连接
    db = PostgreSQLAPI(
        host="localhost",
        port=5432,
        database="project_db",
        user="postgres",
        password="password"
    )
    
    # 文档服务
    docs = GoogleDocsAPI()
    # docs.authenticate()  # 首次使用需要认证
    
    # 表格服务
    sheets = GoogleSheetsAPI()
    # sheets.authenticate()  # 首次使用需要认证
    
    # Excel 本地操作
    excel = ExcelAPI()
    
    # 消息推送
    dingtalk = DingTalkAPI(
        webhook="https://oapi.dingtalk.com/robot/send?access_token=XXX",
        secret="XXX"
    )
    
    wecom = WeComAPI(
        webhook="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=XXX"
    )
    
    feishu = FeishuAPI(
        webhook="https://open.feishu.cn/open-apis/bot/v2/hook/XXX",
        secret="XXX"
    )
    
    # 邮件服务
    email = EmailAPI(
        smtp_server="smtp.gmail.com",
        smtp_port=587,
        username="noreply@company.com",
        password="password"
    )
    
    # JWT 认证
    jwt_auth = JWTAuthAPI(
        secret_key="your-secret-key",
        token_expire_hours=24
    )
    
    # 本地存储
    storage = LocalStorageAPI(base_path="./storage")
    
    logger.info("所有服务已初始化")
    
    return {
        'logger': logger,
        'monitor': monitor,
        'db': db,
        'docs': docs,
        'sheets': sheets,
        'excel': excel,
        'dingtalk': dingtalk,
        'wecom': wecom,
        'feishu': feishu,
        'email': email,
        'jwt_auth': jwt_auth,
        'storage': storage
    }


# ============================================================================
# 步骤 3: 定义工作流函数
# ============================================================================

@monitor.track_function
def complete_task_workflow(task_id: int, task_name: str, assignee: str):
    """
    任务完成自动化工作流
    
    Args:
        task_id: 任务 ID
        task_name: 任务名称
        assignee: 负责人
    """
    logger = services['logger']
    monitor = services['monitor']
    db = services['db']
    dingtalk = services['dingtalk']
    wecom = services['wecom']
    feishu = services['feishu']
    email = services['email']
    excel = services['excel']
    storage = services['storage']
    
    logger.info(f"开始任务完成工作流", task_id=task_id, task_name=task_name)
    
    # 1. 更新数据库任务状态
    with monitor.track_time("更新数据库状态"):
        db.execute_transaction([
            (
                "UPDATE tasks SET status = %s, completed_at = NOW() WHERE id = %s",
                ("completed", task_id)
            ),
            (
                "INSERT INTO task_logs (task_id, action, timestamp) VALUES (%s, %s, NOW())",
                (task_id, "completed")
            )
        ])
        logger.info("任务状态已更新", task_id=task_id)
    
    # 2. 导出任务数据到 Excel
    with monitor.track_time("生成 Excel 报告"):
        excel.create_workbook(f"task_{task_id}_report.xlsx", "任务报告")
        excel.write_headers(["字段", "值"])
        excel.append_rows([
            ["任务 ID", task_id],
            ["任务名称", task_name],
            ["负责人", assignee],
            ["完成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
            ["状态", "已完成"]
        ])
        file_path = f"./reports/task_{task_id}_report.xlsx"
        excel.save(file_path)
        logger.info("Excel 报告已生成", file_path=file_path)
    
    # 3. 保存报告到存储
    with open(file_path, 'rb') as f:
        content = f.read()
    storage.save_file(f"task_{task_id}_report.xlsx", content, subfolder="reports")
    
    # 4. 发送多渠道通知
    with monitor.track_time("发送通知消息"):
        # 钉钉通知
        dingtalk.send_markdown(
            title="✅ 任务完成通知",
            text=f"## 任务完成通知\n\n"
                 f"**任务名称**: {task_name}\n"
                 f"**负责人**: {assignee}\n"
                 f"**完成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                 f"🎉 恭喜完成任务！"
        )
        
        # 企业微信通知
        wecom.send_markdown(
            content=f"## 任务完成通知\n\n"
                   f"任务名称：{task_name}\n"
                   f"负责人：{assignee}\n"
                   f"状态：✅ 已完成"
        )
        
        # 飞书通知
        feishu.send_post(
            title="任务完成通知",
            content=[
                [{"tag": "text", "text": "任务完成通知\n"}],
                [{"tag": "text", "text": "任务名称："}, {"tag": "text", "text": task_name, "style": ["bold"]}],
                [{"tag": "text", "text": "负责人："}, {"tag": "text", "text": assignee}],
                [{"tag": "text", "text": "状态："}, {"tag": "text", "text": "✅ 已完成", "style": ["bold"]}]
            ]
        )
        
        logger.info("多渠道通知已发送", channels=["dingtalk", "wecom", "feishu"])
    
    # 5. 发送邮件确认
    with monitor.track_time("发送邮件"):
        email_content = f"""
        <html>
        <body>
            <h2>✅ 任务完成通知</h2>
            <p><strong>任务名称</strong>: {task_name}</p>
            <p><strong>负责人</strong>: {assignee}</p>
            <p><strong>完成时间</strong>: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>任务已完成，报告已生成并保存。</p>
        </body>
        </html>
        """
        email.send_email(
            to="manager@company.com",
            subject=f"任务完成通知 - {task_name}",
            content=email_content,
            html=True
        )
        logger.info("邮件通知已发送", recipient="manager@company.com")
    
    # 6. 生成认证令牌（用于 API 访问）
    access_token = jwt_auth.generate_token(
        user_id=assignee,
        username=assignee,
        extra_claims={"task_id": task_id}
    )
    
    logger.info(
        "任务完成工作流执行完毕",
        task_id=task_id,
        duration_ms=monitor.metrics.get('complete_task_workflow', [{}])[-1].get('duration_ms', 0)
    )
    
    return {
        "success": True,
        "task_id": task_id,
        "token": access_token,
        "report_path": file_path
    }


# ============================================================================
# 步骤 4: 执行工作流
# ============================================================================

if __name__ == "__main__":
    # 初始化服务
    services = initialize_services()
    
    # 执行任务完成工作流
    result = complete_task_workflow(
        task_id=1001,
        task_name="MVP API 集成验证",
        assignee="全栈工程师 A"
    )
    
    print("\n" + "="*60)
    print("工作流执行结果:")
    print("="*60)
    print(f"✅ 任务 ID: {result['task_id']}")
    print(f"✅ 访问令牌：{result['token'][:50]}...")
    print(f"✅ 报告路径：{result['report_path']}")
    print("="*60)
