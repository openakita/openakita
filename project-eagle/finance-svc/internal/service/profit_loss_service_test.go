package service

import (
	"testing"

	"github.com/project-eagle/finance-svc/internal/model"
)

func TestValidateQuery(t *testing.T) {
	svc := &ProfitLossService{}

	tests := []struct {
		name    string
		query   *model.ProfitLossQuery
		wantErr bool
	}{
		{
			name: "valid query",
			query: &model.ProfitLossQuery{
				TenantID: "tenant-123",
				Year:     2024,
				Month:    3,
			},
			wantErr: false,
		},
		{
			name: "missing tenant_id",
			query: &model.ProfitLossQuery{
				TenantID: "",
				Year:     2024,
				Month:    3,
			},
			wantErr: true,
		},
		{
			name: "invalid year",
			query: &model.ProfitLossQuery{
				TenantID: "tenant-123",
				Year:     1999,
				Month:    3,
			},
			wantErr: true,
		},
		{
			name: "invalid month",
			query: &model.ProfitLossQuery{
				TenantID: "tenant-123",
				Year:     2024,
				Month:    13,
			},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := svc.ValidateQuery(tt.query)
			if (err != nil) != tt.wantErr {
				t.Errorf("ValidateQuery() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}