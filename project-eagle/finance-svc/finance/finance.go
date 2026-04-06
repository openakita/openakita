package finance

import (
	"database/sql"
	"fmt"
	"time"
)

// FinanceService 财务服务
type FinanceService struct {
	db *sql.DB
}

// NewFinanceService 创建财务服务实例
func NewFinanceService(db *sql.DB) *FinanceService {
	return &FinanceService{db: db}
}

// CalculateMonthlyProfitLoss 计算月度损益表
func (s *FinanceService) CalculateMonthlyProfitLoss(tenantID int64, year int, month int) (*MonthlyProfitLoss, error) {
	// 验证参数
	if tenantID <= 0 {
		return nil, fmt.Errorf("invalid tenant ID: %d", tenantID)
	}
	if month < 1 || month > 12 {
		return nil, fmt.Errorf("invalid month: %d", month)
	}

	// 计算月份的起始和结束日期
	startDate := time.Date(year, time.Month(month), 1, 0, 0, 0, 0, time.UTC)
	endDate := startDate.AddDate(0, 1, 0)

	// 查询总收入
	var totalIncome sql.NullFloat64
	err := s.db.QueryRow(`
		SELECT SUM(amount) 
		FROM transactions 
		WHERE tenant_id = ? 
		AND transaction_date >= ? 
		AND transaction_date < ?
		AND type = 'income'`,
		tenantID, startDate, endDate).Scan(&totalIncome)
	if err != nil {
		return nil, fmt.Errorf("failed to query total income: %w", err)
	}

	// 查询总支出
	var totalExpense sql.NullFloat64
	err = s.db.QueryRow(`
		SELECT SUM(amount) 
		FROM transactions 
		WHERE tenant_id = ? 
		AND transaction_date >= ? 
		AND transaction_date < ?
		AND type = 'expense'`,
		tenantID, startDate, endDate).Scan(&totalExpense)
	if err != nil {
		return nil, fmt.Errorf("failed to query total expense: %w", err)
	}

	// 查询交易总数
	var transactionCount sql.NullInt64
	err = s.db.QueryRow(`
		SELECT COUNT(*) 
		FROM transactions 
		WHERE tenant_id = ? 
		AND transaction_date >= ? 
		AND transaction_date < ?`,
		tenantID, startDate, endDate).Scan(&transactionCount)
	if err != nil {
		return nil, fmt.Errorf("failed to query transaction count: %w", err)
	}

	// 查询按类别分组的收入明细
	incomeByCategory, err := s.queryCategorySummary(tenantID, startDate, endDate, "income")
	if err != nil {
		return nil, fmt.Errorf("failed to query income by category: %w", err)
	}

	// 查询按类别分组的支出明细
	expenseByCategory, err := s.queryCategorySummary(tenantID, startDate, endDate, "expense")
	if err != nil {
		return nil, fmt.Errorf("failed to query expense by category: %w", err)
	}

	// 处理NULL值
	var income, expense float64
	var count int64
	if totalIncome.Valid {
		income = totalIncome.Float64
	}
	if totalExpense.Valid {
		expense = totalExpense.Float64
	}
	if transactionCount.Valid {
		count = transactionCount.Int64
	}

	// 构建返回结果
	result := &MonthlyProfitLoss{
		TenantID:          tenantID,
		Year:              year,
		Month:             month,
		TotalIncome:       income,
		TotalExpense:      expense,
		NetProfit:         income - expense,
		IncomeByCategory:  incomeByCategory,
		ExpenseByCategory: expenseByCategory,
		TransactionCount:  count,
		GeneratedAt:       time.Now(),
	}

	return result, nil
}

// queryCategorySummary 查询按类别分组的汇总数据
func (s *FinanceService) queryCategorySummary(tenantID int64, startDate, endDate time.Time, transactionType string) ([]CategorySummary, error) {
	rows, err := s.db.Query(`
		SELECT category, SUM(amount) as total_amount, COUNT(*) as count
		FROM transactions 
		WHERE tenant_id = ? 
		AND transaction_date >= ? 
		AND transaction_date < ?
		AND type = ?
		GROUP BY category
		ORDER BY total_amount DESC`,
		tenantID, startDate, endDate, transactionType)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var summaries []CategorySummary
	for rows.Next() {
		var summary CategorySummary
		err := rows.Scan(&summary.Category, &summary.Amount, &summary.Count)
		if err != nil {
			return nil, err
		}
		summaries = append(summaries, summary)
	}

	if err := rows.Err(); err != nil {
		return nil, err
	}

	return summaries, nil
}

// GetMonthlyProfitLossFromDB 从数据库获取月度损益数据（带连接管理）
func GetMonthlyProfitLossFromDB(config DBConfig, tenantID int64, year int, month int) (*MonthlyProfitLoss, error) {
	db, err := NewDBConnection(config)
	if err != nil {
		return nil, err
	}
	defer db.Close()

	service := NewFinanceService(db)
	return service.CalculateMonthlyProfitLoss(tenantID, year, month)
}
