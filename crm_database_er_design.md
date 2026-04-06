# CRM系统数据库ER结构设计

## 一、核心实体关系概述

```
用户(User) ──┬── 角色(Role) ── 权限(Permission)
             │
             └── 部门(Department)

客户(Customer) ──┬── 联系人(Contact)
                 │
                 ├── 商机(Opportunity) ── 跟进记录(FollowUp)
                 │
                 ├── 合同(Contract) ── 合同明细(ContractItem)
                 │
                 └── 标签(Tag)

产品(Product) ──┬── 商机明细(OpportunityItem)
                │
                └── 合同明细(ContractItem)
```

## 二、核心表结构设计

### 1. 用户与权限模块

#### 用户表 (users)
```sql
CREATE TABLE users (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(50) UNIQUE NOT NULL COMMENT '用户名',
    password_hash VARCHAR(255) NOT NULL COMMENT '密码哈希',
    real_name VARCHAR(50) NOT NULL COMMENT '真实姓名',
    email VARCHAR(100) COMMENT '邮箱',
    phone VARCHAR(20) COMMENT '手机号',
    avatar_url VARCHAR(255) COMMENT '头像URL',
    department_id BIGINT COMMENT '所属部门ID',
    status TINYINT DEFAULT 1 COMMENT '状态：1-启用，0-禁用',
    last_login_at DATETIME COMMENT '最后登录时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_department_id (department_id),
    INDEX idx_status (status)
) COMMENT '系统用户表';
```

#### 角色表 (roles)
```sql
CREATE TABLE roles (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    role_code VARCHAR(50) UNIQUE NOT NULL COMMENT '角色编码',
    role_name VARCHAR(100) NOT NULL COMMENT '角色名称',
    description TEXT COMMENT '角色描述',
    is_system TINYINT DEFAULT 0 COMMENT '是否系统角色：1-是，0-否',
    status TINYINT DEFAULT 1 COMMENT '状态：1-启用，0-禁用',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) COMMENT '角色表';
```

#### 权限表 (permissions)
```sql
CREATE TABLE permissions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    permission_code VARCHAR(100) UNIQUE NOT NULL COMMENT '权限编码',
    permission_name VARCHAR(100) NOT NULL COMMENT '权限名称',
    permission_type ENUM('menu', 'button', 'api') NOT NULL COMMENT '权限类型',
    parent_id BIGINT DEFAULT 0 COMMENT '父权限ID',
    path VARCHAR(255) COMMENT '菜单路径/接口路径',
    icon VARCHAR(50) COMMENT '图标',
    sort_order INT DEFAULT 0 COMMENT '排序',
    status TINYINT DEFAULT 1 COMMENT '状态：1-启用，0-禁用',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_parent_id (parent_id),
    INDEX idx_permission_type (permission_type)
) COMMENT '权限表';
```

#### 用户角色关联表 (user_roles)
```sql
CREATE TABLE user_roles (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL COMMENT '用户ID',
    role_id BIGINT NOT NULL COMMENT '角色ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_user_role (user_id, role_id),
    INDEX idx_user_id (user_id),
    INDEX idx_role_id (role_id)
) COMMENT '用户角色关联表';
```

#### 角色权限关联表 (role_permissions)
```sql
CREATE TABLE role_permissions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    role_id BIGINT NOT NULL COMMENT '角色ID',
    permission_id BIGINT NOT NULL COMMENT '权限ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_role_permission (role_id, permission_id),
    INDEX idx_role_id (role_id),
    INDEX idx_permission_id (permission_id)
) COMMENT '角色权限关联表';
```

#### 部门表 (departments)
```sql
CREATE TABLE departments (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    dept_name VARCHAR(100) NOT NULL COMMENT '部门名称',
    parent_id BIGINT DEFAULT 0 COMMENT '父部门ID',
    dept_path VARCHAR(255) COMMENT '部门路径',
    manager_id BIGINT COMMENT '部门负责人ID',
    sort_order INT DEFAULT 0 COMMENT '排序',
    status TINYINT DEFAULT 1 COMMENT '状态：1-启用，0-禁用',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_parent_id (parent_id),
    INDEX idx_manager_id (manager_id)
) COMMENT '部门表';
```

### 2. 客户管理模块

#### 客户表 (customers)
```sql
CREATE TABLE customers (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    customer_name VARCHAR(200) NOT NULL COMMENT '客户名称',
    company_name VARCHAR(200) COMMENT '公司名称',
    industry VARCHAR(50) COMMENT '所属行业',
    company_size ENUM('tiny', 'small', 'medium', 'large', 'huge') COMMENT '公司规模',
    source VARCHAR(50) COMMENT '客户来源',
    level ENUM('A', 'B', 'C', 'D') DEFAULT 'C' COMMENT '客户等级',
    address VARCHAR(500) COMMENT '地址',
    website VARCHAR(255) COMMENT '网站',
    remark TEXT COMMENT '备注',
    owner_id BIGINT NOT NULL COMMENT '负责人ID',
    department_id BIGINT COMMENT '所属部门ID',
    status TINYINT DEFAULT 1 COMMENT '状态：1-正常，0-流失',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_owner_id (owner_id),
    INDEX idx_department_id (department_id),
    INDEX idx_industry (industry),
    INDEX idx_level (level),
    FULLTEXT INDEX ft_customer_name (customer_name)
) COMMENT '客户表';
```

#### 联系人表 (contacts)
```sql
CREATE TABLE contacts (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    customer_id BIGINT NOT NULL COMMENT '关联客户ID',
    contact_name VARCHAR(50) NOT NULL COMMENT '联系人姓名',
    gender ENUM('male', 'female', 'unknown') DEFAULT 'unknown' COMMENT '性别',
    position VARCHAR(100) COMMENT '职位',
    department VARCHAR(100) COMMENT '部门',
    phone VARCHAR(20) COMMENT '电话',
    mobile VARCHAR(20) COMMENT '手机',
    email VARCHAR(100) COMMENT '邮箱',
    wechat VARCHAR(50) COMMENT '微信',
    is_primary TINYINT DEFAULT 0 COMMENT '是否主要联系人',
    remark TEXT COMMENT '备注',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_customer_id (customer_id),
    INDEX idx_mobile (mobile),
    INDEX idx_email (email)
) COMMENT '联系人表';
```

### 3. 商机管理模块

#### 商机表 (opportunities)
```sql
CREATE TABLE opportunities (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    opportunity_name VARCHAR(200) NOT NULL COMMENT '商机名称',
    customer_id BIGINT NOT NULL COMMENT '关联客户ID',
    contact_id BIGINT COMMENT '主要联系人ID',
    stage ENUM('initial_contact', 'requirement_confirm', 'proposal', 'negotiation', 'won', 'lost') 
        DEFAULT 'initial_contact' COMMENT '商机阶段',
    amount DECIMAL(15,2) COMMENT '预计成交金额',
    currency VARCHAR(10) DEFAULT 'CNY' COMMENT '货币类型',
    probability INT DEFAULT 10 COMMENT '赢率(%)',
    expected_close_date DATE COMMENT '预计成交日期',
    source VARCHAR(50) COMMENT '商机来源',
    description TEXT COMMENT '商机描述',
    lost_reason VARCHAR(200) COMMENT '输单原因',
    owner_id BIGINT NOT NULL COMMENT '负责人ID',
    department_id BIGINT COMMENT '所属部门ID',
    status TINYINT DEFAULT 1 COMMENT '状态：1-进行中，0-已关闭',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_customer_id (customer_id),
    INDEX idx_contact_id (contact_id),
    INDEX idx_stage (stage),
    INDEX idx_owner_id (owner_id),
    INDEX idx_expected_close_date (expected_close_date)
) COMMENT '商机表';
```

#### 商机阶段变更记录表 (opportunity_stage_logs)
```sql
CREATE TABLE opportunity_stage_logs (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    opportunity_id BIGINT NOT NULL COMMENT '商机ID',
    from_stage VARCHAR(50) COMMENT '原阶段',
    to_stage VARCHAR(50) NOT NULL COMMENT '新阶段',
    change_reason TEXT COMMENT '变更原因',
    changed_by BIGINT NOT NULL COMMENT '变更人ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_opportunity_id (opportunity_id),
    INDEX idx_created_at (created_at)
) COMMENT '商机阶段变更记录表';
```

### 4. 跟进管理模块

#### 跟进记录表 (follow_ups)
```sql
CREATE TABLE follow_ups (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    customer_id BIGINT NOT NULL COMMENT '客户ID',
    opportunity_id BIGINT COMMENT '商机ID',
    contact_id BIGINT COMMENT '联系人ID',
    follow_type ENUM('call', 'visit', 'email', 'meeting', 'other') NOT NULL COMMENT '跟进方式',
    content TEXT NOT NULL COMMENT '跟进内容',
    next_plan TEXT COMMENT '下一步计划',
    next_time DATETIME COMMENT '下次跟进时间',
    duration INT COMMENT '跟进时长(分钟)',
    attachments JSON COMMENT '附件信息',
    owner_id BIGINT NOT NULL COMMENT '跟进人ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_customer_id (customer_id),
    INDEX idx_opportunity_id (opportunity_id),
    INDEX idx_owner_id (owner_id),
    INDEX idx_next_time (next_time)
) COMMENT '跟进记录表';
```

### 5. 合同管理模块

#### 合同表 (contracts)
```sql
CREATE TABLE contracts (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    contract_no VARCHAR(50) UNIQUE NOT NULL COMMENT '合同编号',
    contract_name VARCHAR(200) NOT NULL COMMENT '合同名称',
    customer_id BIGINT NOT NULL COMMENT '客户ID',
    opportunity_id BIGINT COMMENT '关联商机ID',
    contact_id BIGINT COMMENT '签约联系人ID',
    amount DECIMAL(15,2) NOT NULL COMMENT '合同金额',
    currency VARCHAR(10) DEFAULT 'CNY' COMMENT '货币类型',
    sign_date DATE COMMENT '签订日期',
    start_date DATE COMMENT '开始日期',
    end_date DATE COMMENT '结束日期',
    payment_terms TEXT COMMENT '付款条款',
    payment_method ENUM('bank_transfer', 'check', 'installment', 'milestone', 'other') 
        DEFAULT 'bank_transfer' COMMENT '付款方式：银行转账/支票/分期/里程碑/其他',
    payment_cycle ENUM('one_time', 'monthly', 'quarterly', 'semi_annual', 'annual') 
        DEFAULT 'one_time' COMMENT '付款周期：一次性/月结/季结/半年/年结',
    delivery_terms TEXT COMMENT '交付条款',
    status ENUM('draft', 'pending', 'active', 'completed', 'terminated') 
        DEFAULT 'draft' COMMENT '合同状态',
    attachment_url VARCHAR(255) COMMENT '合同附件URL',
    remark TEXT COMMENT '备注',
    owner_id BIGINT NOT NULL COMMENT '负责人ID',
    department_id BIGINT COMMENT '所属部门ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_customer_id (customer_id),
    INDEX idx_opportunity_id (opportunity_id),
    INDEX idx_contract_no (contract_no),
    INDEX idx_status (status),
    INDEX idx_sign_date (sign_date)
) COMMENT '合同表';
```

#### 合同审批记录表 (contract_approvals)
```sql
CREATE TABLE contract_approvals (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    contract_id BIGINT NOT NULL COMMENT '合同ID',
    approver_id BIGINT NOT NULL COMMENT '审批人ID',
    approval_status ENUM('pending', 'approved', 'rejected') DEFAULT 'pending' COMMENT '审批状态',
    approval_comment TEXT COMMENT '审批意见',
    approval_time DATETIME COMMENT '审批时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_contract_id (contract_id),
    INDEX idx_approver_id (approver_id)
) COMMENT '合同审批记录表';
```

### 6. 产品管理模块

#### 产品表 (products)
```sql
CREATE TABLE products (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    product_code VARCHAR(50) UNIQUE NOT NULL COMMENT '产品编码',
    product_name VARCHAR(200) NOT NULL COMMENT '产品名称',
    category VARCHAR(50) COMMENT '产品分类',
    unit VARCHAR(20) COMMENT '单位',
    standard_price DECIMAL(15,2) COMMENT '标准价格',
    description TEXT COMMENT '产品描述',
    status TINYINT DEFAULT 1 COMMENT '状态：1-上架，0-下架',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_category (category),
    INDEX idx_status (status)
) COMMENT '产品表';
```

### 7. 标签管理模块

#### 标签表 (tags)
```sql
CREATE TABLE tags (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    tag_name VARCHAR(50) NOT NULL COMMENT '标签名称',
    tag_type ENUM('customer', 'opportunity', 'contact') NOT NULL COMMENT '标签类型',
    color VARCHAR(20) COMMENT '标签颜色',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_tag_type (tag_type)
) COMMENT '标签表';
```

#### 客户标签关联表 (customer_tags)
```sql
CREATE TABLE customer_tags (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    customer_id BIGINT NOT NULL COMMENT '客户ID',
    tag_id BIGINT NOT NULL COMMENT '标签ID',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_customer_tag (customer_id, tag_id),
    INDEX idx_customer_id (customer_id),
    INDEX idx_tag_id (tag_id)
) COMMENT '客户标签关联表';
```

### 8. 数据分析模块

#### 数据快照表 (data_snapshots)
```sql
CREATE TABLE data_snapshots (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    snapshot_date DATE NOT NULL COMMENT '快照日期',
    snapshot_type ENUM('daily', 'weekly', 'monthly') NOT NULL COMMENT '快照类型',
    total_customers INT DEFAULT 0 COMMENT '客户总数',
    total_opportunities INT DEFAULT 0 COMMENT '商机总数',
    total_amount DECIMAL(15,2) DEFAULT 0 COMMENT '商机总金额',
    won_amount DECIMAL(15,2) DEFAULT 0 COMMENT '赢单金额',
    won_count INT DEFAULT 0 COMMENT '赢单数量',
    owner_id BIGINT COMMENT '负责人ID(为空表示全局)',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_snapshot_date (snapshot_date),
    INDEX idx_owner_id (owner_id)
) COMMENT '数据快照表';
```

## 三、关键索引设计

### 高频查询索引
```sql
-- 客户列表查询（负责人+状态）
CREATE INDEX idx_customers_owner_status ON customers(owner_id, status);

-- 商机看板查询（阶段+负责人）
CREATE INDEX idx_opportunities_stage_owner ON opportunities(stage, owner_id);

-- 跟进记录查询（客户+时间）
CREATE INDEX idx_followups_customer_time ON follow_ups(customer_id, created_at);

-- 合同状态查询
CREATE INDEX idx_contracts_status_date ON contracts(status, sign_date);
```

### 全文搜索索引
```sql
-- 客户名称全文搜索
ALTER TABLE customers ADD FULLTEXT INDEX ft_customer_search(customer_name, company_name);

-- 联系人搜索
ALTER TABLE contacts ADD FULLTEXT INDEX ft_contact_search(contact_name, mobile, email);
```

## 四、数据完整性约束

### 外键约束
```sql
-- 联系人关联客户
ALTER TABLE contacts ADD CONSTRAINT fk_contacts_customer 
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE;

-- 商机关联客户
ALTER TABLE opportunities ADD CONSTRAINT fk_opportunities_customer 
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE RESTRICT;

-- 商机关联联系人
ALTER TABLE opportunities ADD CONSTRAINT fk_opportunities_contact 
    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE SET NULL;

-- 跟进记录关联客户
ALTER TABLE follow_ups ADD CONSTRAINT fk_followups_customer 
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE;

-- 合同关联客户
ALTER TABLE contracts ADD CONSTRAINT fk_contracts_customer 
    FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE RESTRICT;
```

### 唯一性约束
```sql
-- 用户名唯一
ALTER TABLE users ADD CONSTRAINT uk_username UNIQUE (username);

-- 合同编号唯一
ALTER TABLE contracts ADD CONSTRAINT uk_contract_no UNIQUE (contract_no);

-- 产品编码唯一
ALTER TABLE products ADD CONSTRAINT uk_product_code UNIQUE (product_code);
```

## 五、ER关系图（Mermaid格式）

```erDiagram
    USERS ||--o{ USER_ROLES : has
    ROLES ||--o{ USER_ROLES : belongs_to
    ROLES ||--o{ ROLE_PERMISSIONS : has
    PERMISSIONS ||--o{ ROLE_PERMISSIONS : belongs_to
    USERS }o--|| DEPARTMENTS : belongs_to
    
    CUSTOMERS ||--o{ CONTACTS : has
    CUSTOMERS ||--o{ OPPORTUNITIES : has
    CUSTOMERS ||--o{ FOLLOW_UPS : has
    CUSTOMERS ||--o{ CONTRACTS : has
    CUSTOMERS ||--o{ CUSTOMER_TAGS : has
    
    CONTACTS ||--o{ OPPORTUNITIES : primary_contact
    CONTACTS ||--o{ FOLLOW_UPS : contacted
    
    OPPORTUNITIES ||--o{ FOLLOW_UPS : has
    OPPORTUNITIES ||--o{ OPPORTUNITY_STAGE_LOGS : has
    OPPORTUNITIES ||--o{ CONTRACTS : generates
    
    CONTRACTS ||--o{ CONTRACT_APPROVALS : requires
    
    TAGS ||--o{ CUSTOMER_TAGS : applied_to
    
    PRODUCTS ||--o{ OPPORTUNITY_ITEMS : included_in
    PRODUCTS ||--o{ CONTRACT_ITEMS : included_in
```

---
*设计完成时间: 2026-03-29*
*基于企业级CRM系统最佳实践*