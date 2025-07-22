import unittest
from unittest.mock import patch, MagicMock, Mock
import os
import lambda_function
import hashlib

class TestLambdaFunction(unittest.TestCase):
    
    def setUp(self):
        """테스트 전 환경 변수 설정"""
        # 필수 환경 변수 설정
        os.environ['SLACK_WEBHOOK'] = 'https://hooks.slack.com/test'
        os.environ['AWS_REGION'] = 'ap-northeast-2'
        os.environ['DYNAMODB_TABLE'] = 'ProcessedNews'
        os.environ['BEDROCK_MODEL_ID'] = 'anthropic.claude-3-haiku-20240307-v1:0'
    
    def test_environment_variables_default_values(self):
        """환경 변수 기본값 테스트"""
        # 모듈을 다시 import하여 환경 변수 재로딩
        import importlib
        importlib.reload(lambda_function)
        
        # 기본값들이 올바르게 설정되었는지 확인
        self.assertEqual(lambda_function.AWS_REGION, 'ap-northeast-2')
        self.assertEqual(lambda_function.DYNAMODB_TABLE, 'ProcessedNews')
        self.assertEqual(lambda_function.MAX_RETRIES, 3)
        self.assertEqual(lambda_function.RETRY_DELAY_BASE, 2)
        self.assertEqual(lambda_function.MAX_SLACK_LENGTH, 3900)
        self.assertEqual(lambda_function.CONTENT_MAX_LENGTH, 3000)
        self.assertEqual(lambda_function.REQUEST_TIMEOUT, 10)
        self.assertEqual(lambda_function.PROCESSING_DELAY, 12)
    
    @patch.dict(os.environ, {
        'AWS_REGION': 'us-east-1',
        'DYNAMODB_TABLE': 'CustomTable',
        'MAX_RETRIES': '5',
        'PROCESSING_DELAY': '15'
    })
    def test_environment_variables_custom_values(self):
        """환경 변수 커스텀 값 테스트"""
        # 모듈을 다시 import하여 환경 변수 재로딩
        import importlib
        importlib.reload(lambda_function)
        
        # 커스텀 값들이 올바르게 설정되었는지 확인
        self.assertEqual(lambda_function.AWS_REGION, 'us-east-1')
        self.assertEqual(lambda_function.DYNAMODB_TABLE, 'CustomTable')
        self.assertEqual(lambda_function.MAX_RETRIES, 5)
        self.assertEqual(lambda_function.PROCESSING_DELAY, 15)
    
    @patch('boto3.resource')
    @patch('boto3.client')
    def test_initialize_aws_clients(self, mock_client, mock_resource):
        """AWS 클라이언트 초기화 테스트"""
        mock_dynamodb = MagicMock()
        mock_bedrock = MagicMock()
        mock_table = MagicMock()
        
        mock_resource.return_value = mock_dynamodb
        mock_client.return_value = mock_bedrock
        mock_dynamodb.Table.return_value = mock_table
        
        # 초기화 함수 호출
        lambda_function.initialize_aws_clients()
        
        # 클라이언트들이 올바르게 초기화되었는지 확인
        mock_resource.assert_called_once_with('dynamodb', region_name=lambda_function.AWS_REGION)
        mock_client.assert_called_once_with('bedrock-runtime', region_name=lambda_function.AWS_REGION)
        mock_dynamodb.Table.assert_called_once_with(lambda_function.DYNAMODB_TABLE)
        
        # 전역 변수들이 설정되었는지 확인
        self.assertIsNotNone(lambda_function.dynamodb)
        self.assertIsNotNone(lambda_function.bedrock_runtime)
        self.assertIsNotNone(lambda_function.table)
    
    def test_generate_news_id(self):
        """뉴스 ID 생성 테스트"""
        link = "https://aws.amazon.com/about-aws/whats-new/2024/01/test-news/"
        expected_id = hashlib.md5(link.encode('utf-8')).hexdigest()
        actual_id = lambda_function.generate_news_id(link)
        self.assertEqual(actual_id, expected_id)
    
    @patch('lambda_function.table')
    def test_is_news_processed_exists(self, mock_table):
        """이미 처리된 뉴스 확인 테스트 - 존재하는 경우"""
        mock_table.get_item.return_value = {'Item': {'id': 'test-id'}}
        result = lambda_function.is_news_processed('test-id')
        self.assertTrue(result)
    
    @patch('lambda_function.table')
    def test_is_news_processed_not_exists(self, mock_table):
        """이미 처리된 뉴스 확인 테스트 - 존재하지 않는 경우"""
        mock_table.get_item.return_value = {}
        result = lambda_function.is_news_processed('test-id')
        self.assertFalse(result)
    
    @patch('lambda_function.table')
    def test_is_initial_run_empty(self, mock_table):
        """초기 실행 확인 테스트 - 빈 테이블"""
        mock_table.scan.return_value = {'Count': 0}
        result = lambda_function.is_initial_run()
        self.assertTrue(result)
    
    @patch('lambda_function.table')
    def test_is_initial_run_not_empty(self, mock_table):
        """초기 실행 확인 테스트 - 데이터가 있는 테이블"""
        mock_table.scan.return_value = {'Count': 5}
        result = lambda_function.is_initial_run()
        self.assertFalse(result)
    
    @patch('lambda_function.table')
    def test_save_processed_news(self, mock_table):
        """처리된 뉴스 저장 테스트"""
        mock_table.put_item.return_value = {}
        result = lambda_function.save_processed_news(
            'test-id', 
            'Test Title', 
            'https://example.com',
            'Test Summary'
        )
        self.assertTrue(result)
        mock_table.put_item.assert_called_once()
    
    @patch('requests.get')
    def test_extract_main_text_success(self, mock_get):
        """웹 페이지 본문 추출 테스트 - 성공"""
        mock_response = MagicMock()
        mock_response.content = b'<main>Test main content</main>'
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response
        
        result = lambda_function.extract_main_text('https://example.com')
        self.assertEqual(result, 'Test main content')
    
    @patch('requests.get')
    def test_extract_main_text_failure(self, mock_get):
        """웹 페이지 본문 추출 테스트 - 실패"""
        mock_get.side_effect = Exception("Network error")
        
        result = lambda_function.extract_main_text('https://example.com')
        self.assertEqual(result, "")
    
    @patch('requests.post')
    def test_send_to_slack_success(self, mock_post):
        """Slack 전송 테스트 - 성공"""
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response
        
        result = lambda_function.send_to_slack('Test message')
        self.assertTrue(result)
    
    @patch('requests.post')
    def test_send_to_slack_failure(self, mock_post):
        """Slack 전송 테스트 - 실패"""
        mock_post.side_effect = Exception("Network error")
        
        result = lambda_function.send_to_slack('Test message')
        self.assertFalse(result)
    
    @patch('feedparser.parse')
    def test_get_rss_news_success(self, mock_parse):
        """RSS 뉴스 가져오기 테스트 - 성공"""
        mock_entry = MagicMock()
        mock_entry.title = 'Test News Title'
        mock_entry.link = 'https://example.com/news'
        mock_entry.published = '2024-01-01'
        mock_entry.published_parsed = (2024, 1, 1, 0, 0, 0, 0, 0, 0)
        
        mock_feed = MagicMock()
        mock_feed.entries = [mock_entry]
        mock_parse.return_value = mock_feed
        
        result = lambda_function.get_rss_news()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['title'], 'Test News Title')
    
    @patch('feedparser.parse')
    def test_get_rss_news_empty(self, mock_parse):
        """RSS 뉴스 가져오기 테스트 - 빈 결과"""
        mock_feed = MagicMock()
        mock_feed.entries = []
        mock_parse.return_value = mock_feed
        
        result = lambda_function.get_rss_news()
        self.assertEqual(len(result), 0)

if __name__ == '__main__':
    unittest.main()