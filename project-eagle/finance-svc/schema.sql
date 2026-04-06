-- StarRocks 表结构定义
-- 用于存储交易数据

CREATE DATABASE IF NOT EXISTS eagle_finance;

USE eagle_finance;

-- 交易记录表
CREATE TABLE IF NOT EXISTS transactions (
    id BIGINT AUTO_INCREMENT,
    tenant_id BIGINT NOT NULL,
    transaction_date DATE NOT NULL,
    category VARCHAR(100) NOT NULL,
    amount DECIMAL(15,2) NOT NULL,
    type VARCHAR(20) NOT NULL COMMENT 'income 或 expense',
    description VARCHAR(500),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id, tenant_id)
) ENGINE=OLAP
DUPLICATE KEY(id, tenant_id)
DISTRIBUTED BY HASH(tenant_id) BUCKETS 10
PROPERTIES (
    "replication_num" = "3",
    "storage_format" = "v2"
);

-- 创建索引（可选）
-- ALTER TABLE transactions ADD INDEX idx_tenant_date (tenant_id, transaction_date) USING BITMAP;
-- ALTER TABLE transactions ADD INDEX idx_category (category) USING BITMAP;

-- 插入示例数据
INSERT INTO transactions (tenant_id, transaction_date, category, amount, type, description) VALUES
(1, '2024-03-01', '销售', 15000.00, 'income', '产品销售收入'),
(1, '2024-03-05', '采购', -5000.00, 'expense', '原材料采购'),
(1, '2024-03-10', '工资', -20000.00, 'expense', '3月份员工工资'),
(1, '2024-03-15', '销售', 8000.00, 'income', '服务收入'),
(1, '2024-03-20', '租金', -3000.00, 'expense', '办公室租金'),
(1, '2024-03-25', '销售', 12000.00, 'income', '产品销售收入');

-- 查询验证
SELECT * FROM transactions WHERE tenant_id = 1 AND transaction_date >= '2024-03-01' AND transaction_date < '2024-04-01';