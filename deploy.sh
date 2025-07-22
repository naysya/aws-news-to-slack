#!/bin/bash

# AWS News to Slack Bot 배포 스크립트
# 사용법: ./deploy.sh [function-name] [slack-webhook-url]

set -e

# 기본값 설정
FUNCTION_NAME=${1:-"aws-news-to-slack"}
SLACK_WEBHOOK=${2:-""}
REGION="ap-northeast-2"
RUNTIME="python3.9"
TIMEOUT=300
MEMORY_SIZE=512

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 AWS News to Slack Bot 배포 시작${NC}"

# Slack Webhook URL 확인
if [ -z "$SLACK_WEBHOOK" ]; then
    echo -e "${RED}❌ Slack Webhook URL이 필요합니다.${NC}"
    echo "사용법: ./deploy.sh [function-name] [slack-webhook-url]"
    exit 1
fi

# AWS CLI 설치 확인
if ! command -v aws &> /dev/null; then
    echo -e "${RED}❌ AWS CLI가 설치되지 않았습니다.${NC}"
    exit 1
fi

# Python 의존성 설치
echo -e "${YELLOW}📦 Python 의존성 설치 중...${NC}"
pip install -r requirements.txt -t . --upgrade

# 배포 패키지 생성
echo -e "${YELLOW}📦 배포 패키지 생성 중...${NC}"
zip -r ${FUNCTION_NAME}.zip . -x "*.git*" "*.md" "*.drawio" "*.sh" "__pycache__/*" "*.pyc"

# Lambda 함수 존재 여부 확인
echo -e "${YELLOW}🔍 Lambda 함수 존재 여부 확인 중...${NC}"
if aws lambda get-function --function-name $FUNCTION_NAME --region $REGION &> /dev/null; then
    echo -e "${YELLOW}🔄 기존 Lambda 함수 업데이트 중...${NC}"
    aws lambda update-function-code \
        --function-name $FUNCTION_NAME \
        --zip-file fileb://${FUNCTION_NAME}.zip \
        --region $REGION
else
    echo -e "${YELLOW}🆕 새 Lambda 함수 생성 중...${NC}"
    
    # IAM 역할 ARN 입력 받기
    echo -e "${YELLOW}IAM 역할 ARN을 입력하세요 (예: arn:aws:iam::123456789012:role/lambda-execution-role):${NC}"
    read -r ROLE_ARN
    
    if [ -z "$ROLE_ARN" ]; then
        echo -e "${RED}❌ IAM 역할 ARN이 필요합니다.${NC}"
        exit 1
    fi
    
    aws lambda create-function \
        --function-name $FUNCTION_NAME \
        --runtime $RUNTIME \
        --role $ROLE_ARN \
        --handler lambda_function.lambda_handler \
        --zip-file fileb://${FUNCTION_NAME}.zip \
        --timeout $TIMEOUT \
        --memory-size $MEMORY_SIZE \
        --region $REGION
fi

# 환경 변수 설정
echo -e "${YELLOW}⚙️ 환경 변수 설정 중...${NC}"

# 필수 환경 변수들 (기본값 또는 오버라이드 값 사용)
AWS_REGION_VAL=${AWS_REGION_OVERRIDE:-$REGION}
DYNAMODB_TABLE_VAL=${DYNAMODB_TABLE_OVERRIDE:-"ProcessedNews"}
BEDROCK_MODEL_ID_VAL=${BEDROCK_MODEL_ID_OVERRIDE:-"anthropic.claude-3-haiku-20240307-v1:0"}

ENV_VARS="SLACK_WEBHOOK=$SLACK_WEBHOOK,AWS_REGION=$AWS_REGION_VAL,DYNAMODB_TABLE=$DYNAMODB_TABLE_VAL,BEDROCK_MODEL_ID=$BEDROCK_MODEL_ID_VAL"

# 선택적 환경 변수들 (사용자가 설정한 경우에만 추가)
if [ ! -z "$MAX_RETRIES_OVERRIDE" ]; then
    ENV_VARS="$ENV_VARS,MAX_RETRIES=$MAX_RETRIES_OVERRIDE"
fi

if [ ! -z "$RETRY_DELAY_BASE_OVERRIDE" ]; then
    ENV_VARS="$ENV_VARS,RETRY_DELAY_BASE=$RETRY_DELAY_BASE_OVERRIDE"
fi

if [ ! -z "$MAX_SLACK_LENGTH_OVERRIDE" ]; then
    ENV_VARS="$ENV_VARS,MAX_SLACK_LENGTH=$MAX_SLACK_LENGTH_OVERRIDE"
fi

if [ ! -z "$CONTENT_MAX_LENGTH_OVERRIDE" ]; then
    ENV_VARS="$ENV_VARS,CONTENT_MAX_LENGTH=$CONTENT_MAX_LENGTH_OVERRIDE"
fi

if [ ! -z "$REQUEST_TIMEOUT_OVERRIDE" ]; then
    ENV_VARS="$ENV_VARS,REQUEST_TIMEOUT=$REQUEST_TIMEOUT_OVERRIDE"
fi

if [ ! -z "$PROCESSING_DELAY_OVERRIDE" ]; then
    ENV_VARS="$ENV_VARS,PROCESSING_DELAY=$PROCESSING_DELAY_OVERRIDE"
fi

aws lambda update-function-configuration \
    --function-name $FUNCTION_NAME \
    --environment Variables="{$ENV_VARS}" \
    --region $AWS_REGION_VAL

# DynamoDB 테이블 생성 (존재하지 않는 경우)
echo -e "${YELLOW}🗄️ DynamoDB 테이블 확인 중...${NC}"
if ! aws dynamodb describe-table --table-name $DYNAMODB_TABLE_VAL --region $AWS_REGION_VAL &> /dev/null; then
    echo -e "${YELLOW}🆕 DynamoDB 테이블 생성 중...${NC}"
    aws dynamodb create-table \
        --table-name $DYNAMODB_TABLE_VAL \
        --attribute-definitions AttributeName=id,AttributeType=S \
        --key-schema AttributeName=id,KeyType=HASH \
        --billing-mode PAY_PER_REQUEST \
        --region $AWS_REGION_VAL
    
    echo -e "${YELLOW}⏳ 테이블 생성 완료 대기 중...${NC}"
    aws dynamodb wait table-exists --table-name $DYNAMODB_TABLE_VAL --region $AWS_REGION_VAL
else
    echo -e "${GREEN}✅ DynamoDB 테이블이 이미 존재합니다.${NC}"
fi

# 정리
rm -f ${FUNCTION_NAME}.zip

echo -e "${GREEN}✅ 배포 완료!${NC}"
echo -e "${GREEN}📋 배포 정보:${NC}"
echo -e "  - 함수명: $FUNCTION_NAME"
echo -e "  - 리전: $REGION"
echo -e "  - 런타임: $RUNTIME"
echo -e "  - 메모리: ${MEMORY_SIZE}MB"
echo -e "  - 타임아웃: ${TIMEOUT}초"

echo -e "${YELLOW}💡 다음 단계:${NC}"
echo -e "  1. EventBridge 규칙을 생성하여 정기 실행 설정"
echo -e "  2. CloudWatch Logs에서 실행 로그 확인"
echo -e "  3. 테스트 실행: aws lambda invoke --function-name $FUNCTION_NAME --region $REGION output.json"

echo -e "${YELLOW}🔧 환경 변수 커스터마이징:${NC}"
echo -e "  배포 전에 다음 환경 변수들을 설정하여 기본값을 변경할 수 있습니다:"
echo -e ""
echo -e "  필수 환경 변수 (기본값 변경 가능):"
echo -e "  - AWS_REGION_OVERRIDE: AWS 리전 변경 (기본: ap-northeast-2)"
echo -e "  - DYNAMODB_TABLE_OVERRIDE: DynamoDB 테이블명 변경 (기본: ProcessedNews)"
echo -e "  - BEDROCK_MODEL_ID_OVERRIDE: Bedrock 모델 ID 변경 (기본: claude-3-haiku)"
echo -e ""
echo -e "  선택적 환경 변수 (성능 튜닝):"
echo -e "  - MAX_RETRIES_OVERRIDE: 최대 재시도 횟수 변경 (기본: 3)"
echo -e "  - RETRY_DELAY_BASE_OVERRIDE: 재시도 딜레이 기본값 변경 (기본: 2초)"
echo -e "  - MAX_SLACK_LENGTH_OVERRIDE: Slack 메시지 최대 길이 변경 (기본: 3900)"
echo -e "  - CONTENT_MAX_LENGTH_OVERRIDE: 본문 최대 길이 변경 (기본: 3000)"
echo -e "  - REQUEST_TIMEOUT_OVERRIDE: HTTP 요청 타임아웃 변경 (기본: 10초)"
echo -e "  - PROCESSING_DELAY_OVERRIDE: 처리 딜레이 변경 (기본: 12초)"
echo -e ""
echo -e "  예시: AWS_REGION_OVERRIDE=us-east-1 PROCESSING_DELAY_OVERRIDE=15 ./deploy.sh my-function https://hooks.slack.com/..."