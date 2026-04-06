package model

import "time"

// MonthlyProfitLoss 月度损益表数据模型
type MonthlyProfitLoss struct {
	TenantID string `json:"tenant_id"`
	Year     int    `json:"year"`
	Month    int    `json:"month"`

	// 收入类
	TotalRevenue    float64 `json:"total_revenue"`     // 总收入
	SalesRevenue    float64 `json:"sales_revenue"`     // 销售收入
	ServiceRevenue  float64 `json:"service_revenue"`   // 服务收入
	OtherRevenue    float64 `json:"other_revenue"`     // 其他收入

	// 支出类
	TotalExpenses        float64 `json:"total_expenses"`         // 总支出
	CostOfGoodsSold      float64 `json:"cost_of_goods_sold"`     // 销售成本
	OperatingExpenses    float64 `json:"operating_expenses"`     // 运营费用
	AdministrativeExpenses float64 `json:"administrative_expenses"` // 管理费用
	FinancialExpenses    float64 `json:"financial_expenses"`     // 财务费用
	OtherExpenses        float64 `json:"other_expenses"`         // 其他支出

	// 利润
	GrossProfit     float64 `json:"gross_profit"`      // 毛利润
	OperatingProfit float64 `json:"operating_profit"`  // 营业利润
	NetProfit       float64 `json:"net_profit"`        // 净利润

	// 元数据
	CalculatedAt time.Time `json:"calculated_at"` // 计算时间
	IsFinalized  bool      `json:"is_finalized"`  // 是否已定稿
}

// ProfitLossQuery 损益表查询参数
type ProfitLossQuery struct {
	TenantID string `json:"tenant_id"`
	Year     int    `json:"year"`
	Month    int    `json:"month"`
}

// RevenueSummary 收入汇总（从 StarRocks 查询）
type RevenueSummary struct {
	Category string  `json:"category"` // sales, service, other
	Amount   float64 `json:"amount"`
}

// ExpenseSummary 支出汇总（从 StarRocks 查询）
type ExpenseSummary struct {
	Category string  `json:"category"` // cogs, operating, administrative, financial, other
	Amount   float64 `json:"amount"`
}