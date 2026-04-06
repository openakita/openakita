package finance

import "time"

// MonthlyProfitLoss 月度损益表数据结构
type MonthlyProfitLoss struct {
	TenantID       int64               `json:"tenant_id"`
	Year           int                 `json:"year"`
	Month          int                 `json:"month"`
	TotalIncome    float64             `json:"total_income"`
	TotalExpense   float64             `json:"total_expense"`
	NetProfit      float64             `json:"net_profit"`
	IncomeByCategory  []CategorySummary `json:"income_by_category"`
	ExpenseByCategory []CategorySummary `json:"expense_by_category"`
	TransactionCount  int64             `json:"transaction_count"`
	GeneratedAt    time.Time           `json:"generated_at"`
}

// CategorySummary 类别汇总
type CategorySummary struct {
	Category string  `json:"category"`
	Amount   float64 `json:"amount"`
	Count    int64   `json:"count"`
}

// Transaction 交易记录（用于数据库映射）
type Transaction struct {
	ID               int64     `json:"id"`
	TenantID         int64     `json:"tenant_id"`
	TransactionDate  time.Time `json:"transaction_date"`
	Category         string    `json:"category"`
	Amount           float64   `json:"amount"`
	Type             string    `json:"type"`
	Description      string    `json:"description"`
	CreatedAt        time.Time `json:"created_at"`
}

// DBConfig 数据库配置
type DBConfig struct {
	Host     string
	Port     int
	User     string
	Password string
	Database string
}
