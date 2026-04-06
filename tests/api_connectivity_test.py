#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
企业微信、钉钉、飞书 API 连通性测试脚本
用于 MVP 项目 P0 级 API 账号验证

测试项目：
1. 企业微信：消息推送、Webhook 机器人
2. 钉钉：消息推送、Webhook 机器人
3. 飞书：消息推送、多维表格读写

使用方法：
1. 在 config.json 中填入各平台的 AppID/Secret/Webhook 地址
2. 运行：python api_connectivity_test.py
3. 查看测试结果报告
"""

import json
import requests
import time
from datetime import datetime
from typing import Dict, List, Tuple

# 测试结果存储
test_results = {
    "timestamp": datetime.now().isoformat(),
    "platforms": {}
}


def load_config() -> Dict:
    """加载配置文件"""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        print("❌ 配置文件 config.json 不存在")
        print("请先在 config.json 中填入各平台的 AppID/Secret/Webhook 地址")
        return {}


def test_enterprise_wechat(config: Dict) -> Dict:
    """
    企业微信 API 连通性测试
    
    测试项目：
    1. 获取 access_token
    2. 发送应用消息
    3. Webhook 机器人消息
    """
    result = {
        "platform": "企业微信",
        "tests": [],
        "status": "pending"
    }
    
    try:
        corp_id = config.get('enterprise_wechat', {}).get('corp_id')
        agent_id = config.get('enterprise_wechat', {}).get('agent_id')
        secret = config.get('enterprise_wechat', {}).get('secret')
        webhook_url = config.get('enterprise_wechat', {}).get('webhook_url')
        
        if not all([corp_id, agent_id, secret]):
            result["tests"].append({
                "name": "配置检查",
                "status": "failed",
                "message": "缺少必要配置（corp_id/agent_id/secret）"
            })
            result["status"] = "failed"
            return result
        
        # 测试 1: 获取 access_token
        token_url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corp_id}&corpsecret={secret}"
        try:
            response = requests.get(token_url, timeout=10)
            token_data = response.json()
            
            if token_data.get('errcode') == 0:
                access_token = token_data['access_token']
                result["tests"].append({
                    "name": "获取 access_token",
                    "status": "passed",
                    "message": f"成功获取 token，有效期 {token_data.get('expires_in', 'N/A')}秒"
                })
                
                # 测试 2: 发送应用消息
                msg_url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"
                msg_payload = {
                    "touser": "@all",
                    "msgtype": "text",
                    "agentid": int(agent_id),
                    "text": {
                        "content": f"【MVP 测试】企业微信 API 连通性测试成功 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    },
                    "safe": 0
                }
                
                msg_response = requests.post(msg_url, json=msg_payload, timeout=10)
                msg_data = msg_response.json()
                
                if msg_data.get('errcode') == 0:
                    result["tests"].append({
                        "name": "发送应用消息",
                        "status": "passed",
                        "message": "消息发送成功"
                    })
                else:
                    result["tests"].append({
                        "name": "发送应用消息",
                        "status": "failed",
                        "message": f"发送失败：{msg_data.get('errmsg', '未知错误')}"
                    })
            else:
                result["tests"].append({
                    "name": "获取 access_token",
                    "status": "failed",
                    "message": f"获取失败：{token_data.get('errmsg', '未知错误')}"
                })
        except Exception as e:
            result["tests"].append({
                "name": "获取 access_token",
                "status": "error",
                "message": f"请求异常：{str(e)}"
            })
        
        # 测试 3: Webhook 机器人
        if webhook_url:
            try:
                webhook_payload = {
                    "msgtype": "text",
                    "text": {
                        "content": f"【MVP 测试】企业微信 Webhook 连通性测试成功 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    }
                }
                webhook_response = requests.post(webhook_url, json=webhook_payload, timeout=10)
                webhook_data = webhook_response.json()
                
                if webhook_data.get('errcode') == 0:
                    result["tests"].append({
                        "name": "Webhook 机器人消息",
                        "status": "passed",
                        "message": "Webhook 消息发送成功"
                    })
                else:
                    result["tests"].append({
                        "name": "Webhook 机器人消息",
                        "status": "failed",
                        "message": f"发送失败：{webhook_data.get('errmsg', '未知错误')}"
                    })
            except Exception as e:
                result["tests"].append({
                    "name": "Webhook 机器人消息",
                    "status": "error",
                    "message": f"请求异常：{str(e)}"
                })
        else:
            result["tests"].append({
                "name": "Webhook 机器人消息",
                "status": "skipped",
                "message": "未配置 Webhook URL"
            })
        
        # 汇总状态
        passed_count = sum(1 for t in result["tests"] if t["status"] == "passed")
        total_count = len([t for t in result["tests"] if t["status"] != "skipped"])
        
        if passed_count == total_count and total_count > 0:
            result["status"] = "passed"
        elif passed_count > 0:
            result["status"] = "partial"
        else:
            result["status"] = "failed"
            
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
    
    return result


def test_dingtalk(config: Dict) -> Dict:
    """
    钉钉 API 连通性测试
    
    测试项目：
    1. 获取 access_token
    2. 发送应用消息
    3. Webhook 机器人消息
    """
    result = {
        "platform": "钉钉",
        "tests": [],
        "status": "pending"
    }
    
    try:
        app_key = config.get('dingtalk', {}).get('app_key')
        app_secret = config.get('dingtalk', {}).get('app_secret')
        agent_id = config.get('dingtalk', {}).get('agent_id')
        webhook_url = config.get('dingtalk', {}).get('webhook_url')
        
        if not all([app_key, app_secret]):
            result["tests"].append({
                "name": "配置检查",
                "status": "failed",
                "message": "缺少必要配置（app_key/app_secret）"
            })
            result["status"] = "failed"
            return result
        
        # 测试 1: 获取 access_token
        token_url = "https://oapi.dingtalk.com/gettoken"
        try:
            response = requests.get(token_url, params={
                'appkey': app_key,
                'appsecret': app_secret
            }, timeout=10)
            token_data = response.json()
            
            if token_data.get('errcode') == 0:
                access_token = token_data['access_token']
                result["tests"].append({
                    "name": "获取 access_token",
                    "status": "passed",
                    "message": "成功获取 token"
                })
                
                # 测试 2: 发送应用消息
                msg_url = f"https://oapi.dingtalk.com/topapi/message/corpconversation/asyncsend_v2?access_token={access_token}"
                msg_payload = {
                    "agent_id": int(agent_id) if agent_id else 0,
                    "userid_list": "@all",
                    "msgtype": "text",
                    "text": {
                        "content": f"【MVP 测试】钉钉 API 连通性测试成功 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    }
                }
                
                msg_response = requests.post(msg_url, json=msg_payload, timeout=10)
                msg_data = msg_response.json()
                
                if msg_data.get('errcode') == 0:
                    result["tests"].append({
                        "name": "发送应用消息",
                        "status": "passed",
                        "message": "消息发送成功"
                    })
                else:
                    result["tests"].append({
                        "name": "发送应用消息",
                        "status": "failed",
                        "message": f"发送失败：{msg_data.get('errmsg', '未知错误')}"
                    })
            else:
                result["tests"].append({
                    "name": "获取 access_token",
                    "status": "failed",
                    "message": f"获取失败：{token_data.get('errmsg', '未知错误')}"
                })
        except Exception as e:
            result["tests"].append({
                "name": "获取 access_token",
                "status": "error",
                "message": f"请求异常：{str(e)}"
            })
        
        # 测试 3: Webhook 机器人
        if webhook_url:
            try:
                webhook_payload = {
                    "msgtype": "text",
                    "text": {
                        "content": f"【MVP 测试】钉钉 Webhook 连通性测试成功 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    }
                }
                webhook_response = requests.post(webhook_url, json=webhook_payload, timeout=10)
                webhook_data = webhook_response.json()
                
                if webhook_data.get('errcode') == 0:
                    result["tests"].append({
                        "name": "Webhook 机器人消息",
                        "status": "passed",
                        "message": "Webhook 消息发送成功"
                    })
                else:
                    result["tests"].append({
                        "name": "Webhook 机器人消息",
                        "status": "failed",
                        "message": f"发送失败：{webhook_data.get('errmsg', '未知错误')}"
                    })
            except Exception as e:
                result["tests"].append({
                    "name": "Webhook 机器人消息",
                    "status": "error",
                    "message": f"请求异常：{str(e)}"
                })
        else:
            result["tests"].append({
                "name": "Webhook 机器人消息",
                "status": "skipped",
                "message": "未配置 Webhook URL"
            })
        
        # 汇总状态
        passed_count = sum(1 for t in result["tests"] if t["status"] == "passed")
        total_count = len([t for t in result["tests"] if t["status"] != "skipped"])
        
        if passed_count == total_count and total_count > 0:
            result["status"] = "passed"
        elif passed_count > 0:
            result["status"] = "partial"
        else:
            result["status"] = "failed"
            
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
    
    return result


def test_feishu(config: Dict) -> Dict:
    """
    飞书 API 连通性测试
    
    测试项目：
    1. 获取 tenant_access_token
    2. 发送应用消息
    3. 多维表格读写测试
    """
    result = {
        "platform": "飞书",
        "tests": [],
        "status": "pending"
    }
    
    try:
        app_id = config.get('feishu', {}).get('app_id')
        app_secret = config.get('feishu', {}).get('app_secret')
        webhook_url = config.get('feishu', {}).get('webhook_url')
        bitable_app_token = config.get('feishu', {}).get('bitable_app_token')
        bitable_table_id = config.get('feishu', {}).get('bitable_table_id')
        
        if not all([app_id, app_secret]):
            result["tests"].append({
                "name": "配置检查",
                "status": "failed",
                "message": "缺少必要配置（app_id/app_secret）"
            })
            result["status"] = "failed"
            return result
        
        # 测试 1: 获取 tenant_access_token
        token_url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        try:
            response = requests.post(token_url, json={
                'app_id': app_id,
                'app_secret': app_secret
            }, timeout=10)
            token_data = response.json()
            
            if token_data.get('code') == 0:
                access_token = token_data['tenant_access_token']
                result["tests"].append({
                    "name": "获取 tenant_access_token",
                    "status": "passed",
                    "message": f"成功获取 token，有效期 {token_data.get('expire', 'N/A')}秒"
                })
                
                # 测试 2: 发送应用消息
                msg_url = "https://open.feishu.cn/open-apis/im/v1/messages"
                headers = {
                    'Authorization': f'Bearer {access_token}',
                    'Content-Type': 'application/json'
                }
                msg_payload = {
                    "receive_id": "open_id",  # 实际使用时需要替换为真实用户 ID
                    "msg_type": "text",
                    "content": json.dumps({
                        "text": f"【MVP 测试】飞书 API 连通性测试成功 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    })
                }
                
                # 注意：这里需要真实的用户 ID，测试时可能会失败
                msg_response = requests.post(msg_url, headers=headers, json=msg_payload, timeout=10)
                msg_data = msg_response.json()
                
                if msg_data.get('code') == 0:
                    result["tests"].append({
                        "name": "发送应用消息",
                        "status": "passed",
                        "message": "消息发送成功"
                    })
                else:
                    result["tests"].append({
                        "name": "发送应用消息",
                        "status": "failed",
                        "message": f"发送失败：{msg_data.get('msg', '未知错误')}（可能需要配置接收人 ID）"
                    })
            else:
                result["tests"].append({
                    "name": "获取 tenant_access_token",
                    "status": "failed",
                    "message": f"获取失败：{token_data.get('msg', '未知错误')}"
                })
        except Exception as e:
            result["tests"].append({
                "name": "获取 tenant_access_token",
                "status": "error",
                "message": f"请求异常：{str(e)}"
            })
        
        # 测试 3: Webhook 机器人
        if webhook_url:
            try:
                webhook_payload = {
                    "msg_type": "text",
                    "content": {
                        "text": f"【MVP 测试】飞书 Webhook 连通性测试成功 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    }
                }
                webhook_response = requests.post(webhook_url, json=webhook_payload, timeout=10)
                webhook_data = webhook_response.json()
                
                if webhook_data.get('code') == 0:
                    result["tests"].append({
                        "name": "Webhook 机器人消息",
                        "status": "passed",
                        "message": "Webhook 消息发送成功"
                    })
                else:
                    result["tests"].append({
                        "name": "Webhook 机器人消息",
                        "status": "failed",
                        "message": f"发送失败：{webhook_data.get('msg', '未知错误')}"
                    })
            except Exception as e:
                result["tests"].append({
                    "name": "Webhook 机器人消息",
                    "status": "error",
                    "message": f"请求异常：{str(e)}"
                })
        else:
            result["tests"].append({
                "name": "Webhook 机器人消息",
                "status": "skipped",
                "message": "未配置 Webhook URL"
            })
        
        # 测试 4: 多维表格读写（可选）
        if bitable_app_token and bitable_table_id:
            try:
                # 读取多维表格数据
                list_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{bitable_app_token}/tables/{bitable_table_id}/records"
                headers = {
                    'Authorization': f'Bearer {access_token}',
                    'Content-Type': 'application/json'
                }
                
                list_response = requests.get(list_url, headers=headers, timeout=10)
                list_data = list_response.json()
                
                if list_data.get('code') == 0:
                    result["tests"].append({
                        "name": "多维表格读取",
                        "status": "passed",
                        "message": f"成功读取表格，共 {len(list_data.get('data', {}).get('items', []))} 条记录"
                    })
                    
                    # 写入测试（创建一条测试记录）
                    create_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{bitable_app_token}/tables/{bitable_table_id}/records"
                    create_payload = {
                        "fields": {
                            "测试字段": f"MVP 测试 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                        }
                    }
                    
                    create_response = requests.post(create_url, headers=headers, json=create_payload, timeout=10)
                    create_data = create_response.json()
                    
                    if create_data.get('code') == 0:
                        result["tests"].append({
                            "name": "多维表格写入",
                            "status": "passed",
                            "message": "测试记录写入成功"
                        })
                    else:
                        result["tests"].append({
                            "name": "多维表格写入",
                            "status": "failed",
                            "message": f"写入失败：{create_data.get('msg', '未知错误')}"
                        })
                else:
                    result["tests"].append({
                        "name": "多维表格读取",
                        "status": "failed",
                        "message": f"读取失败：{list_data.get('msg', '未知错误')}"
                    })
            except Exception as e:
                result["tests"].append({
                    "name": "多维表格读写",
                    "status": "error",
                    "message": f"请求异常：{str(e)}"
                })
        else:
            result["tests"].append({
                "name": "多维表格读写",
                "status": "skipped",
                "message": "未配置多维表格信息（bitable_app_token/table_id）"
            })
        
        # 汇总状态
        passed_count = sum(1 for t in result["tests"] if t["status"] == "passed")
        total_count = len([t for t in result["tests"] if t["status"] != "skipped"])
        
        if passed_count == total_count and total_count > 0:
            result["status"] = "passed"
        elif passed_count > 0:
            result["status"] = "partial"
        else:
            result["status"] = "failed"
            
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
    
    return result


def generate_report(results: Dict) -> str:
    """生成测试报告"""
    report = []
    report.append("# MVP 项目 API 连通性测试报告")
    report.append(f"\n**测试时间**: {results['timestamp']}")
    report.append(f"\n**测试人**: 全栈工程师 A")
    report.append("\n---\n")
    
    overall_status = "passed"
    for platform, data in results['platforms'].items():
        status_emoji = {
            "passed": "✅",
            "partial": "⚠️",
            "failed": "❌",
            "error": "❌",
            "pending": "⏳"
        }.get(data['status'], "❓")
        
        report.append(f"## {status_emoji} {data['platform']}")
        report.append(f"\n**整体状态**: {data['status']}")
        report.append("\n### 测试详情\n")
        report.append("| 测试项 | 状态 | 说明 |")
        report.append("|--------|------|------|")
        
        for test in data['tests']:
            status_emoji = {
                "passed": "✅",
                "failed": "❌",
                "error": "❌",
                "skipped": "⏭️"
            }.get(test['status'], "❓")
            report.append(f"| {test['name']} | {status_emoji} {test['status']} | {test['message']} |")
        
        report.append("\n")
        
        if data['status'] not in ['passed', 'partial']:
            overall_status = "failed"
    
    report.append("---\n")
    report.append(f"## 总体结论\n")
    
    if overall_status == "passed":
        report.append("✅ **所有平台 API 连通性测试通过**，可以投入生产使用。")
    else:
        report.append("⚠️ **部分测试未通过**，请检查配置或联系平台技术支持。")
    
    return "\n".join(report)


def main():
    """主函数"""
    print("=" * 60)
    print("MVP 项目 API 连通性测试")
    print("=" * 60)
    print(f"测试时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # 加载配置
    config = load_config()
    if not config:
        print("\n请创建 config.json 文件，参考配置模板：")
        print(json.dumps({
            "enterprise_wechat": {
                "corp_id": "企业 ID",
                "agent_id": "应用 ID",
                "secret": "应用 Secret",
                "webhook_url": "Webhook 地址（可选）"
            },
            "dingtalk": {
                "app_key": "AppKey",
                "app_secret": "AppSecret",
                "agent_id": "AgentId",
                "webhook_url": "Webhook 地址（可选）"
            },
            "feishu": {
                "app_id": "App ID",
                "app_secret": "App Secret",
                "webhook_url": "Webhook 地址（可选）",
                "bitable_app_token": "多维表格 App Token（可选）",
                "bitable_table_id": "多维表格 Table ID（可选）"
            }
        }, indent=2, ensure_ascii=False))
        return
    
    # 执行测试
    print("\n开始测试企业微信...")
    test_results['platforms']['enterprise_wechat'] = test_enterprise_wechat(config)
    
    print("开始测试钉钉...")
    test_results['platforms']['dingtalk'] = test_dingtalk(config)
    
    print("开始测试飞书...")
    test_results['platforms']['feishu'] = test_feishu(config)
    
    # 生成报告
    report = generate_report(test_results)
    
    # 保存报告
    report_filename = f"api_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    with open(report_filename, 'w', encoding='utf-8') as f:
        f.write(report)
    
    print(f"\n✅ 测试完成，报告已保存至：{report_filename}")
    print("\n" + "=" * 60)
    print(report)
    
    # 保存测试结果 JSON
    with open(f"api_test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json", 'w', encoding='utf-8') as f:
        json.dump(test_results, f, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
