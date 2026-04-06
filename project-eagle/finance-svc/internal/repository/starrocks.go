package repository

import (
	"context"
	"database/sql"
	"fmt"
	"time"

	_ "github.com/go-sql-driver/mysql"
	"github.com/project-eagle/finance-svc/internal/model"
)

// StarRocksConfig StarRocks 连接配置
type StarRocksConfig struct {
	Host         string        `json:"host"`
	Port         int           `json:"port"`
	Username     string        `json:"username"`
	Password     string        `json:"password"`
	Database     string        `json:"database"`
	MaxOpenConns int           `json:"max_open_conns"`
	MaxIdleConns int           `json:"max_idle_conns"`
	MaxLifetime  time.Duration `json:"max_lifetime"`
}

// StarRocksRepository StarRocks 数据访问层
type StarRocksRepository struct {
	db *sql.DB
}

// NewStarRocksRepository 创建 StarRocks 仓库实例
func NewStarRocksRepository(config *StarRocksConfig) (*StarRocksRepository, error) {
	dsn := fmt.Sprintf("%s:%s@tcp(%s:%d)/%s?parseTime=true",
		config.Username,
		config.Password,
		config.Host,
		config.Port,
		config.Database,
	)

	db, err := sql.Open("mysql", dsn)
	if err != nil {
		return nil, fmt.Errorf("failed to open database: %w", err)
	}

	// 连接池配置
	db.SetMaxOpenConns(config.MaxOpenConns)
	db.SetMaxIdleConns(config.MaxIdleConns)
	db.SetConnMaxLifetime(config.MaxLifetime)

	// 测试连接
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	if err := db.PingContext(ctx); err != nil {
		db.Close()
		return nil, fmt.Errorf("failed to ping database: %w", err)
	}

	return &StarRocksRepository{db: db}, nil
}

// Close 关闭数据库连接
func (r *StarRocksRepository) Close() error {
	return r.db.Close()
}

// QueryMonthlyRevenue 查询月度收入汇总
func (r *StarRocksRepository) QueryMonthlyRevenue(ctx context.Context, query *model.ProfitLossQuery) ([]model.RevenueSummary, error) {
	sqlQuery := `
		SELECT 
			category,
			SUM(amount) as total_amount
		FROM monthly_revenue_detail
		WHERE tenant_id = ?
			AND year = ?
			AND month = ?
		GROUP BY category
	`

	rows, err := r.db.QueryContext(ctx, sqlQuery, query.TenantID, query.Year, query.Month)
	if err != nil {
		return nil, fmt.Errorf("failed to query revenue: %w", err)
	}
	defer rows.Close()

	var summaries []model.RevenueSummary
	for rows.Next() {
		var summary model.RevenueSummary
		if err := rows.Scan(&summary.Category, &summary.Amount); err != nil {
			return nil, fmt.Errorf("failed to scan revenue row: %w", err)
		}
		summaries = append(summaries, summary)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("revenue rows iteration error: %w", err)
	}

	return summaries, nil
}

// QueryMonthlyExpense 查询月度支出汇总
func (r *StarRocksRepository) QueryMonthlyExpense(ctx context.Context, query *model.ProfitLossQuery) ([]model.ExpenseSummary, error) {
	sqlQuery := `
		SELECT 
			category,
			SUM(amount) as total_amount
		FROM monthly_expense_detail
		WHERE tenant_id = ?
			AND year = ?
			AND month = ?
		GROUP BY category
	`

	rows, err := r.db.QueryContext(ctx, sqlQuery, query.TenantID, query.Year, query.Month)
	if err != nil {
		return nil, fmt.Errorf("failed to query expense: %w", err)
	}
	defer rows.Close()

	var summaries []model.ExpenseSummary
	for rows.Next() {
		var summary model.ExpenseSummary
		if err := rows.Scan(&summary.Category, &summary.Amount); err != nil {
			return nil, fmt.Errorf("failed to scan expense row: %w", err)
		}
		summaries = append(summaries, summary)
	}

	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("expense rows iteration error: %w", err)
	}

	return summaries, nil
}