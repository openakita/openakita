package main

import (
	"context"
	"fmt"
	"log"
	"net"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/project-eagle/finance-svc/internal/handler"
	"github.com/project-eagle/finance-svc/internal/repository"
	"github.com/project-eagle/finance-svc/internal/service"
	pb "github.com/project-eagle/finance-svc/proto/finance/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"
)

func main() {
	// 1. 加载配置（示例使用硬编码，实际应从配置文件或环境变量读取）
	config := &repository.StarRocksConfig{
		Host:         getEnv("STARROCKS_HOST", "localhost"),
		Port:         9030,
		Username:     getEnv("STARROCKS_USER", "root"),
		Password:     getEnv("STARROCKS_PASSWORD", ""),
		Database:     getEnv("STARROCKS_DATABASE", "finance"),
		MaxOpenConns: 50,
		MaxIdleConns: 10,
		MaxLifetime:  5 * time.Minute,
	}

	// 2. 初始化 StarRocks 连接
	repo, err := repository.NewStarRocksRepository(config)
	if err != nil {
		log.Fatalf("Failed to connect to StarRocks: %v", err)
	}
	defer repo.Close()

	// 3. 创建服务层
	profitLossSvc := service.NewProfitLossService(repo)

	// 4. 创建 gRPC handler
	financeHandler := handler.NewFinanceHandler(profitLossSvc)

	// 5. 启动 gRPC 服务器
	port := getEnv("GRPC_PORT", "50051")
	lis, err := net.Listen("tcp", fmt.Sprintf(":%s", port))
	if err != nil {
		log.Fatalf("Failed to listen: %v", err)
	}

	grpcServer := grpc.NewServer()
	
	// 注册服务
	pb.RegisterFinanceServiceServer(grpcServer, financeHandler)
	
	// 注册健康检查
	healthServer := health.NewServer()
	healthpb.RegisterHealthServer(grpcServer, healthServer)

	// 启动服务器
	go func() {
		log.Printf("Finance service starting on port %s", port)
		if err := grpcServer.Serve(lis); err != nil {
			log.Fatalf("Failed to serve: %v", err)
		}
	}()

	// 6. 优雅关闭
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit

	log.Println("Shutting down server...")
	
	// 停止接受新连接
	grpcServer.GracefulStop()
	
	// 关闭数据库连接
	_, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	
	if err := repo.Close(); err != nil {
		log.Printf("Error closing database: %v", err)
	}
	
	log.Println("Server stopped")
}

func getEnv(key, defaultValue string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return defaultValue
}