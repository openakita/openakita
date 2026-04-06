"""
Mock 配置文件
定义各 API 的 Mock 响应和延迟
"""
from typing import Dict, Any

MOCK_CONFIG: Dict[str, Dict[str, Any]] = {
    "email": {
        "enabled": True,
        "delay": 0.5,
        "success_rate": 0.98,
        "responses": {
            "send": {
                "message_id": "mock-email-12345",
                "status": "sent",
                "provider": "aliyun"
            }
        }
    },
    
    "wecom": {
        "enabled": True,
        "delay": 0.3,
        "success_rate": 0.99,
        "responses": {
            "send_message": {
                "errcode": 0,
                "errmsg": "ok",
                "message_id": "mock-wecom-67890"
            },
            "send_robot": {
                "errcode": 0,
                "errmsg": "ok"
            }
        }
    },
    
    "dingtalk": {
        "enabled": True,
        "delay": 0.3,
        "success_rate": 0.99,
        "responses": {
            "send_message": {
                "errcode": 0,
                "errmsg": "ok",
                "message_id": "mock-ding-11111"
            },
            "send_robot": {
                "errcode": 0,
                "errmsg": "ok"
            }
        }
    },
    
    "crm": {
        "enabled": True,
        "delay": 0.8,
        "success_rate": 0.95,
        "provider": "xiaoshouyi",
        "responses": {
            "create_lead": {
                "lead_id": "mock-lead-22222",
                "status": "created",
                "name": "Mock Lead"
            },
            "query_lead": {
                "leads": [
                    {"id": "1", "name": "Test Lead", "phone": "13800138000"}
                ],
                "total": 1
            },
            "create_customer": {
                "customer_id": "mock-customer-33333",
                "status": "created"
            },
            "query_customer": {
                "customers": [],
                "total": 0
            }
        }
    },
    
    "spreadsheet": {
        "enabled": True,
        "delay": 0.6,
        "success_rate": 0.97,
        "provider": "feishu",
        "responses": {
            "read": {
                "rows": [
                    {"name": "张三", "age": 25, "city": "北京"},
                    {"name": "李四", "age": 30, "city": "上海"}
                ],
                "total": 2
            },
            "write": {
                "row_id": "mock-row-44444",
                "status": "created"
            },
            "update": {
                "updated": True,
                "rows_affected": 1
            }
        }
    },
    
    "database": {
        "enabled": True,
        "delay": 0.4,
        "success_rate": 0.99,
        "provider": "postgresql",
        "responses": {
            "query": {
                "rows": [],
                "count": 0
            },
            "execute": {
                "affected_rows": 0
            },
            "insert": {
                "id": 12345,
                "status": "success"
            }
        }
    },
    
    "oss": {
        "enabled": True,
        "delay": 1.0,
        "success_rate": 0.98,
        "provider": "aliyun",
        "responses": {
            "upload": {
                "url": "https://mock.oss.example.com/file-55555.txt",
                "etag": "mock-etag-abc123",
                "size": 1024
            },
            "download": {
                "content": b"Mock file content",
                "size": 1024
            },
            "delete": {
                "deleted": True,
                "status": "success"
            }
        }
    },
    
    "sms": {
        "enabled": True,
        "delay": 0.5,
        "success_rate": 0.97,
        "provider": "aliyun",
        "responses": {
            "send": {
                "message_id": "mock-sms-66666",
                "status": "sent",
                "phone": "138****0000"
            }
        }
    },
    
    "webhook": {
        "enabled": True,
        "delay": 0.8,
        "success_rate": 0.95,
        "responses": {
            "post": {
                "status": "success",
                "response_code": 200,
                "response_body": {"result": "ok"}
            },
            "get": {
                "status": "success",
                "response_code": 200,
                "response_body": {"data": "mock data"}
            }
        }
    },
    
    "calendar": {
        "enabled": True,
        "delay": 0.6,
        "success_rate": 0.96,
        "provider": "google",
        "responses": {
            "create_event": {
                "event_id": "mock-event-77777",
                "status": "confirmed",
                "html_link": "https://calendar.google.com/mock-event"
            },
            "query_events": {
                "events": [
                    {
                        "id": "1",
                        "summary": "Mock Meeting",
                        "start": "2026-03-12T10:00:00Z",
                        "end": "2026-03-12T11:00:00Z"
                    }
                ],
                "total": 1
            },
            "update_event": {
                "updated": True,
                "event_id": "mock-event-77777"
            },
            "delete_event": {
                "deleted": True,
                "event_id": "mock-event-77777"
            }
        }
    },
    
    "transform": {
        "enabled": True,
        "delay": 0.2,
        "success_rate": 0.99,
        "responses": {
            "json_to_csv": {
                "rows": 10,
                "columns": 5,
                "csv_preview": "name,age,city\n张三，25，北京\n..."
            },
            "csv_to_json": {
                "records": [
                    {"name": "张三", "age": 25, "city": "北京"}
                ],
                "total": 1
            },
            "xml_to_json": {
                "data": {"root": {"item": "value"}}
            },
            "json_to_xml": {
                "xml": "<?xml version='1.0'?><root><item>value</item></root>"
            }
        }
    }
}

# 全局 Mock 启用开关
MOCK_ENABLED = True
