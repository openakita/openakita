package service

import (
	"context"
	"fmt"
	"time"

	"github.com/project-eagle/finance-svc/internal/model"
	"github.com/project-eagle/finance-svc/internal/repository"
)

// ProfitLossService 损益表业务逻辑层
type ProfitLossService struct {
	repo *repository.StarRocksRepository
}

// NewProfitLossService 创建损益表服务实例
func NewProfitLossService(repo *repository.StarRocksRepository) *ProfitLossService {
	return &ProfitLossService{repo: repo}
}

// CalculateMonthlyProfitLoss 计算月度损益表
func (s *ProfitLossService) CalculateMonthlyProfitLoss(ctx context.Context, query *model.ProfitLossQuery) (*model.MonthlyProfitLoss, error) {
	// 1. 查询收入汇总
	revenueSummaries, err := s.repo.QueryMonthlyRevenue(ctx, query)
	if err != nil {
		return nil, fmt.Errorf("failed to query revenue: %w", err)
	}

	// 2. 查询支出汇总
	expenseSummaries, err := s.repo.QueryMonthlyExpense(ctx, query)
	if err != nil {
		return nil, fmt.Errorf("failed to query expense: %w", err)
	}

	// 3. 构建损益表
	result := &model.MonthlyProfitLoss{
		TenantID:     query.TenantID,
		Year:         query.Year,
		Month:        query.Month,
		CalculatedAt: time.Now(),
		IsFinalized:  false,
	}

	// 4. 计算收入
	for _, rev := range revenueSummaries {
		switch rev.Category {
		case "sales":
			result.SalesRevenue = rev.Amount
		case "service":
			result.ServiceRevenue = rev.Amount
		case "other":
			result.OtherRevenue = rev.Amount
		}
		result.TotalRevenue += rev.Amount
	}

	// 5. 计算支出
	for _, exp := range expenseSummaries {
		switch exp.Category {
		case "cogs":
			result.CostOfGoodsSold = exp.Amount
		case "operating":
			result.OperatingExpenses = exp.Amount
		case "administrative":
			result.AdministrativeExpenses = exp.Amount
		case "financial":
			result.FinancialExpenses = exp.Amount
		case "other":
			result.OtherExpenses = exp.Amount
		}
		result.TotalExpenses += exp.Amount
	}

	// 6. 计算利润指标
	result.GrossProfit = result.TotalRevenue - result.CostOfGoodsSold
	result.OperatingProfit = result.GrossProfit - result.OperatingExpenses - 
		result.AdministrativeExpenses - result.FinancialExpenses
	result.NetProfit = result.OperatingProfit - result.OtherExpenses

	return result, nil
}

// ValidateQuery 验证查询参数
func (s *ProfitLossService) ValidateQuery(query *model.ProfitLossQuery) error {
	if query.TenantID == "" {
		return fmt.Errorf("tenant_id is required")
	}
	if query.Year < 2000 || query.Year > 2100 {
		return fmt.Errorf("year must be between 2000 and 2100")
	}
	if query.Month < 1 || query.Month > 12 {
		return fmt.Errorf("month must be between 1 and 12")
	}
	return nil
}