package handler

import (
	"context"
	"time"

	"github.com/project-eagle/finance-svc/internal/model"
	"github.com/project-eagle/finance-svc/internal/service"
	pb "github.com/project-eagle/finance-svc/proto/finance/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// FinanceHandler gRPC 服务处理器
type FinanceHandler struct {
	pb.UnimplementedFinanceServiceServer
	service *service.ProfitLossService
}

// NewFinanceHandler 创建 gRPC handler 实例
func NewFinanceHandler(svc *service.ProfitLossService) *FinanceHandler {
	return &FinanceHandler{service: svc}
}

// GetMonthlyProfitLoss 实现 gRPC 接口
func (h *FinanceHandler) GetMonthlyProfitLoss(ctx context.Context, req *pb.GetMonthlyProfitLossRequest) (*pb.GetMonthlyProfitLossResponse, error) {
	// 1. 转换请求参数
	query := &model.ProfitLossQuery{
		TenantID: req.GetTenantId(),
		Year:     int(req.GetYear()),
		Month:    int(req.GetMonth()),
	}

	// 2. 验证参数
	if err := h.service.ValidateQuery(query); err != nil {
		return nil, status.Errorf(codes.InvalidArgument, "invalid request: %v", err)
	}

	// 3. 计算损益表
	result, err := h.service.CalculateMonthlyProfitLoss(ctx, query)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "failed to calculate profit loss: %v", err)
	}

	// 4. 转换为 protobuf 响应
	response := &pb.GetMonthlyProfitLossResponse{
		Data: convertToProto(result),
	}

	return response, nil
}

// convertToProto 将模型转换为 protobuf 消息
func convertToProto(m *model.MonthlyProfitLoss) *pb.MonthlyProfitLoss {
	return &pb.MonthlyProfitLoss{
		TenantId:               m.TenantID,
		Year:                   int32(m.Year),
		Month:                  int32(m.Month),
		TotalRevenue:           m.TotalRevenue,
		SalesRevenue:           m.SalesRevenue,
		ServiceRevenue:         m.ServiceRevenue,
		OtherRevenue:           m.OtherRevenue,
		TotalExpenses:          m.TotalExpenses,
		CostOfGoodsSold:        m.CostOfGoodsSold,
		OperatingExpenses:      m.OperatingExpenses,
		AdministrativeExpenses: m.AdministrativeExpenses,
		FinancialExpenses:      m.FinancialExpenses,
		OtherExpenses:          m.OtherExpenses,
		GrossProfit:            m.GrossProfit,
		OperatingProfit:        m.OperatingProfit,
		NetProfit:              m.NetProfit,
		CalculatedAt:           m.CalculatedAt.Format(time.RFC3339),
		IsFinalized:            m.IsFinalized,
	}
}