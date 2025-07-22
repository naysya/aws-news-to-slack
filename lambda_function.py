# lambda_function.py (AWS News to Slack Bot)
import requests
import boto3
import os
import json
import feedparser
import hashlib
import time
from datetime import datetime
from bs4 import BeautifulSoup
from botocore.exceptions import ClientError

# 환경 변수에서 설정값 가져오기 (필수)
SLACK_WEBHOOK = os.environ['SLACK_WEBHOOK']
AWS_REGION = os.environ['AWS_REGION']
DYNAMODB_TABLE = os.environ['DYNAMODB_TABLE']
BEDROCK_MODEL_ID = os.environ['BEDROCK_MODEL_ID']

# RSS 피드 URL (하드코딩)
RSS_FEED_URL = 'https://aws.amazon.com/about-aws/whats-new/recent/feed/'

# 설정값 (환경 변수로 설정 가능, 기본값 제공)
MAX_RETRIES = int(os.environ.get('MAX_RETRIES', '3'))
RETRY_DELAY_BASE = int(os.environ.get('RETRY_DELAY_BASE', '2'))
MAX_SLACK_LENGTH = int(os.environ.get('MAX_SLACK_LENGTH', '3900'))
CONTENT_MAX_LENGTH = int(os.environ.get('CONTENT_MAX_LENGTH', '3000'))
REQUEST_TIMEOUT = int(os.environ.get('REQUEST_TIMEOUT', '10'))
PROCESSING_DELAY = int(os.environ.get('PROCESSING_DELAY', '12'))

# 클라이언트 초기화 (지연 초기화로 변경)
dynamodb = None
bedrock_runtime = None
table = None

def initialize_aws_clients():
    """AWS 클라이언트 초기화"""
    global dynamodb, bedrock_runtime, table
    
    if dynamodb is None:
        dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
        bedrock_runtime = boto3.client('bedrock-runtime', region_name=AWS_REGION)
        table = dynamodb.Table(DYNAMODB_TABLE)
        print(f"[INFO] AWS 클라이언트 초기화 완료 - Region: {AWS_REGION}, Table: {DYNAMODB_TABLE}")

def generate_news_id(link):
    """뉴스 링크로부터 고유 ID 생성"""
    return hashlib.md5(link.encode('utf-8')).hexdigest()

def is_initial_run():
    """DynamoDB 테이블이 비어있는지 확인 (초기 실행 여부)"""
    try:
        response = table.scan(Limit=1)
        return response['Count'] == 0
    except Exception as e:
        print(f"[ERROR] DynamoDB 스캔 실패: {e}")
        return False

def is_news_processed(news_id):
    """뉴스가 이미 처리되었는지 확인"""
    try:
        response = table.get_item(Key={'id': news_id})
        return 'Item' in response
    except Exception as e:
        print(f"[ERROR] DynamoDB 조회 실패 (ID: {news_id}): {e}")
        return False

def save_processed_news(news_id, title, link, summary=None):
    """처리된 뉴스를 DynamoDB에 저장"""
    try:
        item = {
            'id': news_id,
            'title': title,
            'link': link,
            'processed_at': datetime.utcnow().isoformat(),
        }
        if summary:
            item['summary'] = summary
            
        table.put_item(Item=item)
        print(f"[INFO] DynamoDB 저장 완료: {title[:50]}...")
        return True
    except Exception as e:
        print(f"[ERROR] DynamoDB 저장 실패: {e}")
        return False

def extract_main_text(url):
    """웹 페이지에서 본문 텍스트 추출"""
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, "html.parser")
        
        # main 태그 우선 시도
        main_block = soup.select_one("main")
        if main_block and main_block.get_text(strip=True):
            text = main_block.get_text(separator='\n', strip=True)
        else:
            # article 태그 시도
            article_block = soup.select_one("article")
            if article_block and article_block.get_text(strip=True):
                text = article_block.get_text(separator='\n', strip=True)
            else:
                # content div 시도
                content_divs = soup.find_all('div', class_=['content', 'main-content', 'post-content'])
                text = ""
                for div in content_divs:
                    text = div.get_text(separator='\n', strip=True)
                    if len(text) > 100:  # 충분한 길이의 텍스트가 있으면
                        break
        
        # 본문 길이 제한 (토큰 사용량 감소)
        if text and len(text) > CONTENT_MAX_LENGTH:
            text = text[:CONTENT_MAX_LENGTH] + "..."
            print(f"[INFO] 본문이 너무 길어서 {CONTENT_MAX_LENGTH}자로 제한: {url}")
        
        if not text:
            print(f"[WARN] 본문을 찾을 수 없음: {url}")
            return ""
        
        return text
        
    except Exception as e:
        print(f"[ERROR] 웹 페이지 본문 추출 실패 ({url}): {e}")
        return ""

def summarize_with_bedrock(title, body, date, link, max_retries=MAX_RETRIES):
    """Bedrock Claude를 사용하여 뉴스 요약"""
    
    # 사용자 제공 프롬프트 사용
    prompt = f"""다음은 AWS의 새로운 서비스 또는 기능 업데이트 뉴스입니다. 이 내용을 요약해서 Slack 메시지로 작성해주세요.
언어는 한국어로 번역해서 전달해주세요.

출력 형식은 다음과 같이 구성합니다:

1. 첫 줄: 🎉 이모지 + 뉴스 제목 (한 줄로 간결히 요약)
2. 두 번째 줄: 🗓 발표일 (예: 2025년 6월 9일 형식)
3. 그 아래 한두 문장으로 핵심 요약 (기능, 목적, 기대 효과 등 자연스럽게 설명)
4. ✨ 주요 특징 제목 줄
5. 주요 특징 항목을 아래 형식으로 나열

각 특징 항목 형식은 다음과 같이 구성합니다:

1️⃣ 항목 제목 (한 줄로 요약)  
- 해당 기능 또는 특징에 대한 설명 (1~2줄, 효과나 유용성 중심)

2️⃣ ...  
- ...

항목은 총 2~3개로 구성하며, 이모지는 실제 숫자 이모지(1️⃣, 2️⃣, 3️⃣ 등)를 사용합니다.  
전체 메시지는 Slack 메시지 형식으로 마크다운 없이, 자연스럽고 명확하게 구성해주세요.
전체 응답은 반드시 한국어로 출력해주세요.

마지막 줄에는 🔗 자세히 보기: (뉴스 URL)을 추가해주세요.
---

제목: {title}
발표일: {date}
뉴스 원문:
{body}
뉴스 링크: {link}"""

    for attempt in range(max_retries):
        try:
            # Bedrock Claude 3.5 Sonnet 호출
            payload = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 4000,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.3
            }
            
            response = bedrock_runtime.invoke_model(
                modelId=BEDROCK_MODEL_ID,
                body=json.dumps(payload),
                contentType="application/json"
            )
            
            response_body = json.loads(response['body'].read())
            summary = response_body['content'][0]['text']
            
            print(f"[INFO] Bedrock 요약 성공: {title[:50]}...")
            return {'success': True, 'summary': summary}
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            print(f"[ERROR] Bedrock 호출 실패 (시도 {attempt + 1}/{max_retries}): {error_code}")
            
            if error_code == 'ThrottlingException':
                if attempt < max_retries - 1:
                    delay = (RETRY_DELAY_BASE ** (attempt + 1)) + 5  # 훨씬 더 긴 딜레이
                    print(f"[INFO] ThrottlingException - {delay}초 후 재시도...")
                    time.sleep(delay)
                    continue
            else:
                break
                
        except Exception as e:
            print(f"[ERROR] Bedrock 호출 실패 (시도 {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                delay = RETRY_DELAY_BASE ** (attempt + 1)
                print(f"[INFO] {delay}초 후 재시도...")
                time.sleep(delay)
    
    # 모든 재시도 실패 시 실패 정보 반환
    print(f"[ERROR] Bedrock 요약 최종 실패: {title}")
    fallback_message = f"🎉 {title}\n🗓 {date}\n\n요약 생성에 실패했습니다.\n\n🔗 자세히 보기: {link}"
    return {'success': False, 'summary': fallback_message}

def get_rss_news():
    """RSS 피드에서 뉴스 목록 가져오기"""
    try:
        feed = feedparser.parse(RSS_FEED_URL)
        
        if not feed.entries:
            print(f"[WARN] RSS 피드에서 뉴스를 가져올 수 없음: {RSS_FEED_URL}")
            return []
        
        news_list = []
        for entry in feed.entries:
            try:
                # 발표일 파싱
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    published_date = datetime(*entry.published_parsed[:6])
                else:
                    published_date = datetime.utcnow()
                
                news_list.append({
                    "title": entry.title.strip(),
                    "link": entry.link.strip(),
                    "date": entry.published if hasattr(entry, 'published') else published_date.strftime('%Y-%m-%d'),
                    "datetime": published_date
                })
            except Exception as e:
                print(f"[ERROR] RSS 항목 파싱 실패: {e}")
                continue
        
        print(f"[INFO] RSS에서 {len(news_list)}개 뉴스 수집 완료")
        return news_list
        
    except Exception as e:
        print(f"[ERROR] RSS 피드 파싱 실패: {e}")
        return []

def send_to_slack(message):
    """Slack으로 메시지 전송"""
    try:
        if len(message) > MAX_SLACK_LENGTH:
            print(f"[WARN] 메시지가 너무 긺 ({len(message)}자), 잘라서 전송")
            message = message[:MAX_SLACK_LENGTH] + "...\n(메시지가 잘렸습니다)"
        
        response = requests.post(
            SLACK_WEBHOOK,
            json={"text": message},
            timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        print("[INFO] Slack 전송 성공")
        return True
        
    except Exception as e:
        print(f"[ERROR] Slack 전송 실패: {e}")
        return False

def lambda_handler(event, context):
    """Lambda 핸들러 함수"""
    print("[INFO] AWS News to Slack 처리 시작")
    
    try:
        # AWS 클라이언트 초기화
        initialize_aws_clients()
        
        # RSS 뉴스 가져오기
        news_items = get_rss_news()
        if not news_items:
            return {'statusCode': 200, 'body': 'No news found'}
        
        # 초기 실행 확인
        if is_initial_run():
            print("[INFO] 초기 실행 감지 - 기록만 수행하고 알림은 전송하지 않음")
            
            # 모든 뉴스를 DynamoDB에 기록만 함
            for item in news_items:
                news_id = generate_news_id(item['link'])
                save_processed_news(news_id, item['title'], item['link'])
            
            return {
                'statusCode': 200, 
                'body': f'Initial run completed - recorded {len(news_items)} news items'
            }
        
        # 새로운 뉴스 필터링 및 순차 처리
        new_news_count = 0
        summary_success_count = 0
        slack_success_count = 0
        
        # 첫 번째 요청 전 딜레이
        print("[INFO] Bedrock 요청 제한 방지를 위해 초기 3초 대기...")
        time.sleep(3)
        
        for item in news_items:
            news_id = generate_news_id(item['link'])
            
            # 이미 처리된 뉴스인지 확인
            if is_news_processed(news_id):
                continue
            
            new_news_count += 1
            print(f"[INFO] 새 뉴스 처리 중 ({new_news_count}): {item['title'][:50]}...")
            
            # 본문 추출
            main_text = extract_main_text(item['link'])
            if not main_text:
                print(f"[WARN] 본문이 없어 건너뜀: {item['title']}")
                # 본문이 없어도 DynamoDB에는 기록하여 중복 방지
                save_processed_news(news_id, item['title'], item['link'])
                continue
            
            # Bedrock으로 요약 생성 (순차 처리)
            bedrock_result = summarize_with_bedrock(
                item['title'], 
                main_text, 
                item['date'], 
                item['link']
            )
            
            # 요약 성공 여부 체크
            if bedrock_result['success']:
                summary_success_count += 1
                print(f"[INFO] 요약 성공: {item['title'][:50]}...")
            else:
                print(f"[WARN] 요약 실패, 기본 메시지 사용: {item['title'][:50]}...")
            
            # Slack으로 전송
            if send_to_slack(bedrock_result['summary']):
                slack_success_count += 1
            
            # DynamoDB에 저장
            save_processed_news(news_id, item['title'], item['link'], bedrock_result['summary'])
            
            # ThrottlingException 방지를 위한 더 긴 딜레이
            print(f"[INFO] 다음 뉴스 처리를 위해 {PROCESSING_DELAY}초 대기...")
            time.sleep(PROCESSING_DELAY)
        
        result_message = f"처리 완료 - 새 뉴스: {new_news_count}개, 요약 성공: {summary_success_count}개, Slack 전송 성공: {slack_success_count}개"
        print(f"[INFO] {result_message}")
        
        return {
            'statusCode': 200,
            'body': result_message
        }
        
    except Exception as e:
        error_message = f"Lambda 실행 중 오류 발생: {e}"
        print(f"[ERROR] {error_message}")
        return {
            'statusCode': 500,
            'body': error_message
        }


