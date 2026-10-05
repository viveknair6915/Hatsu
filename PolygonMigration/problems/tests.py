from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from unittest.mock import patch, MagicMock
from problems.models import Problem, SampleTestCase, ProblemTestCase, ProblemTag
from problems.storage import BaseStorageService, AzureStorageService

User = get_user_model()

class AuthenticationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.regular_user = User.objects.create_user(
            email='user@example.com',
            username='regularuser',
            password='password123',
            first_name='John',
            last_name='Doe',
            contact_number='1234567890',
            college='Engineering',
            gender='MALE'
        )
        self.staff_user = User.objects.create_user(
            email='staff@example.com',
            username='staffuser',
            password='password123',
            first_name='Staff',
            last_name='Member',
            contact_number='1234567890',
            college='Engineering',
            gender='MALE',
            is_staff=True
        )

    def test_unauthenticated_user_redirected_to_login(self):
        response = self.client.get(reverse('problems:index'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/users/login/', response.url)

    def test_non_staff_user_redirected_to_login(self):
        self.client.login(username='user@example.com', password='password123')
        response = self.client.get(reverse('problems:index'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/users/login/', response.url)

    def test_staff_user_can_access_migration_interface(self):
        self.client.login(username='staff@example.com', password='password123')
        response = self.client.get(reverse('problems:index'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'problems/index.html')


class ProblemMigrationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_user(
            email='admin@example.com',
            username='adminuser',
            password='password123',
            first_name='Admin',
            last_name='User',
            contact_number='1234567890',
            college='Engineering',
            gender='MALE',
            is_staff=True
        )
        self.client.login(username='admin@example.com', password='password123')

    @patch('problems.views.PolygonAPI')
    def test_problem_migration_requires_difficulty(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        mock_api.get_problem_info.return_value = {'name': 'Sample Problem', 'timeLimit': 1000, 'memoryLimit': 256}
        mock_api.download_and_extract_package.return_value = ''
        mock_api.get_test_cases_from_redis.return_value = []
        mock_api._make_request.return_value = []

        response = self.client.post(reverse('problems:index'), {
            'problem_id': '101',
            'migrate_to_db': '1',
            'difficulty': '',
            'tags': ['math', 'dp']
        })
        self.assertIn('Please select a valid difficulty level', response.context['error'])
        self.assertEqual(Problem.objects.filter(polygon_id='101').count(), 0)

    @patch('problems.views.PolygonAPI')
    def test_problem_migration_requires_at_least_two_tags(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        mock_api.get_problem_info.return_value = {'name': 'Sample Problem', 'timeLimit': 1000, 'memoryLimit': 256}
        mock_api.download_and_extract_package.return_value = ''
        mock_api.get_test_cases_from_redis.return_value = []
        mock_api._make_request.return_value = []

        response = self.client.post(reverse('problems:index'), {
            'problem_id': '102',
            'migrate_to_db': '1',
            'difficulty': 'medium',
            'tags': ['math']
        })
        self.assertIn('Please select at least two tags', response.context['error'])
        self.assertEqual(Problem.objects.filter(polygon_id='102').count(), 0)

    @patch('problems.views.PolygonAPI')
    def test_problem_migration_and_re_migration_updates_deterministically(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        mock_api.get_problem_info.return_value = {'name': 'Graph Cycles', 'timeLimit': 1000, 'memoryLimit': 256}
        mock_api.download_and_extract_package.return_value = '<div class="title">Graph Cycles</div><div class="legend">Find cycles</div>'
        mock_api.get_test_cases_from_redis.return_value = [
            {'index': 1, 'input': '3\n1 2\n', 'output': 'YES\n', 'is_sample': True}
        ]
        mock_api._make_request.return_value = []

        response1 = self.client.post(reverse('problems:index'), {
            'problem_id': '201',
            'migrate_to_db': '1',
            'difficulty': 'easy',
            'tags': ['graphs', 'dfs']
        })
        self.assertEqual(Problem.objects.filter(polygon_id='201').count(), 1)
        prob = Problem.objects.get(polygon_id='201')
        self.assertEqual(prob.difficulty, 'easy')
        self.assertEqual(set(t.tag_name for t in prob.extra_tags.all()), {'graphs', 'dfs'})
        self.assertEqual(SampleTestCase.objects.filter(problem=prob).count(), 1)

        response2 = self.client.post(reverse('problems:index'), {
            'problem_id': '201',
            'migrate_to_db': '1',
            'difficulty': 'hard',
            'tags': ['trees', 'bfs', 'dijkstra']
        })
        self.assertEqual(Problem.objects.filter(polygon_id='201').count(), 1)
        prob.refresh_from_db()
        self.assertEqual(prob.difficulty, 'hard')
        self.assertEqual(set(t.tag_name for t in prob.extra_tags.all()), {'trees', 'bfs', 'dijkstra'})


class EdgeCaseTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff_user = User.objects.create_user(
            email='evaluator@example.com',
            username='evaluator',
            password='password123',
            first_name='Evaluator',
            last_name='User',
            contact_number='1234567890',
            college='Engineering',
            gender='MALE',
            is_staff=True
        )
        self.client.login(username='evaluator@example.com', password='password123')

    @patch('problems.views.PolygonAPI')
    def test_edge_case_1_zero_sample_test_cases(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        mock_api.get_problem_info.return_value = {'name': 'Zero Samples Problem', 'timeLimit': 1000, 'memoryLimit': 256}
        mock_api.download_and_extract_package.return_value = '<div class="title">Zero Samples</div>'
        fifteen_tests = [{'index': i, 'input': f'in_{i}', 'output': f'out_{i}', 'is_sample': False} for i in range(1, 16)]
        mock_api.get_test_cases_from_redis.return_value = fifteen_tests
        mock_api._make_request.return_value = []

        response = self.client.post(reverse('problems:index'), {
            'problem_id': '301',
            'migrate_to_db': '1',
            'difficulty': 'medium',
            'tags': ['sorting', 'greedy']
        })
        self.assertEqual(Problem.objects.filter(polygon_id='301').count(), 1)
        problem = Problem.objects.get(polygon_id='301')
        self.assertEqual(SampleTestCase.objects.filter(problem=problem).count(), 0)

    @patch('problems.views.PolygonAPI')
    def test_edge_case_2_stale_test_cases_cleanup(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        mock_api.get_problem_info.return_value = {'name': 'Dynamic Tests Problem', 'timeLimit': 1000, 'memoryLimit': 256}
        mock_api.download_and_extract_package.return_value = '<div class="title">Dynamic Tests</div>'
        mock_api._make_request.return_value = []

        initial_20_tests = [{'index': i, 'input': f'input_{i}', 'output': f'output_{i}', 'is_sample': False, 'description': f'desc_{i}'} for i in range(1, 21)]
        mock_api.get_test_cases_from_redis.return_value = initial_20_tests

        self.client.post(reverse('problems:index'), {
            'problem_id': '401',
            'migrate_to_db': '1',
            'difficulty': 'hard',
            'tags': ['geometry', 'math']
        })
        self.client.post(reverse('problems:index'), {
            'problem_id': '401',
            'migrate_test_cases_to_db': '1'
        })
        problem = Problem.objects.get(polygon_id='401')
        self.assertEqual(ProblemTestCase.objects.filter(problem=problem).count(), 20)

        reduced_12_tests = [{'index': i, 'input': f'new_input_{i}', 'output': f'new_output_{i}', 'is_sample': False, 'description': f'new_desc_{i}'} for i in range(1, 13)]
        mock_api.get_test_cases_from_redis.return_value = reduced_12_tests

        self.client.post(reverse('problems:index'), {
            'problem_id': '401',
            'migrate_test_cases_to_db': '1'
        })
        self.assertEqual(ProblemTestCase.objects.filter(problem=problem).count(), 12)
        remaining_orders = list(ProblemTestCase.objects.filter(problem=problem).order_by('order').values_list('order', flat=True))
        self.assertEqual(remaining_orders, list(range(1, 13)))

    @patch('problems.views.PolygonAPI')
    def test_edge_case_3_identical_titles_different_polygon_ids(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        mock_api.download_and_extract_package.return_value = '<div class="title">Two Sum</div>'
        mock_api.get_test_cases_from_redis.return_value = []
        mock_api._make_request.return_value = []

        mock_api.get_problem_info.return_value = {'name': 'Two Sum', 'timeLimit': 1000, 'memoryLimit': 256}
        res1 = self.client.post(reverse('problems:index'), {
            'problem_id': '1001',
            'migrate_to_db': '1',
            'difficulty': 'easy',
            'tags': ['arrays', 'hash-table']
        })
        self.assertEqual(Problem.objects.filter(polygon_id='1001').count(), 1)
        prob1 = Problem.objects.get(polygon_id='1001')
        self.assertEqual(prob1.slug, 'two-sum')

        mock_api.get_problem_info.return_value = {'name': 'Two Sum', 'timeLimit': 2000, 'memoryLimit': 512}
        res2 = self.client.post(reverse('problems:index'), {
            'problem_id': '1002',
            'migrate_to_db': '1',
            'difficulty': 'medium',
            'tags': ['two-pointers', 'sorting']
        })
        self.assertEqual(Problem.objects.filter(polygon_id='1002').count(), 1)
        prob2 = Problem.objects.get(polygon_id='1002')
        self.assertEqual(prob2.slug, 'two-sum-1002')
        self.assertNotEqual(prob1.id, prob2.id)

    @patch('problems.views.PolygonAPI')
    def test_edge_case_4_full_test_case_data_preservation(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        mock_api.get_problem_info.return_value = {'name': 'Large Test Problem', 'timeLimit': 1000, 'memoryLimit': 256}
        mock_api.download_and_extract_package.return_value = '<div class="title">Large Test</div>'
        mock_api._make_request.return_value = []

        large_input = "42\n" + "1000000000 " * 500
        large_output = "999999999\n" * 300
        self.assertGreater(len(large_input), 260)
        self.assertGreater(len(large_output), 260)

        mock_api.get_test_cases_from_redis.return_value = [
            {'index': 1, 'input': large_input, 'output': large_output, 'is_sample': False, 'description': 'large case'}
        ]

        self.client.post(reverse('problems:index'), {
            'problem_id': '501',
            'migrate_to_db': '1',
            'difficulty': 'hard',
            'tags': ['dp', 'bitmask']
        })
        self.client.post(reverse('problems:index'), {
            'problem_id': '501',
            'migrate_test_cases_to_db': '1'
        })
        problem = Problem.objects.get(polygon_id='501')
        tc = ProblemTestCase.objects.get(problem=problem, order=1)
        self.assertEqual(tc.input, large_input)
        self.assertEqual(tc.output, large_output)


class StorageAbstractionTests(TestCase):
    def test_azure_storage_upload_path_formatting(self):
        storage = AzureStorageService(
            account_url='https://fakeaccount.blob.core.windows.net',
            tenant_id='fake-tenant',
            client_id='fake-client',
            username='fake-user',
            password='fake-password',
            container_name='testcases'
        )
        mock_blob_client = MagicMock()
        storage._blob_service_client = mock_blob_client

        storage.upload_test_case(problem_id=42, test_number=5, input_data='1 2\n', output_data='3\n')
        calls = mock_blob_client.get_blob_client.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].kwargs['blob'], 'test_cases/42/5')
        self.assertEqual(calls[1].kwargs['blob'], 'test_cases/42/5.a')

    def test_azure_storage_delete_test_cases_removes_old_blobs(self):
        storage = AzureStorageService(
            account_url='https://fakeaccount.blob.core.windows.net',
            tenant_id='fake-tenant',
            client_id='fake-client',
            username='fake-user',
            password='fake-password',
            container_name='testcases'
        )
        mock_blob_client = MagicMock()
        mock_container = MagicMock()
        blob_mock1 = MagicMock()
        blob_mock1.name = 'test_cases/99/1'
        blob_mock2 = MagicMock()
        blob_mock2.name = 'test_cases/99/1.a'
        mock_container.list_blobs.return_value = [blob_mock1, blob_mock2]
        mock_blob_client.get_container_client.return_value = mock_container
        storage._blob_service_client = mock_blob_client

        storage.delete_test_cases(99)
        mock_container.delete_blob.assert_any_call('test_cases/99/1')
        mock_container.delete_blob.assert_any_call('test_cases/99/1.a')


class RedisFallbackTests(TestCase):
    @patch('problems.views.PolygonAPI')
    def test_redis_unavailable_falls_back_to_polygon(self, mock_api_cls):
        mock_api = mock_api_cls.return_value
        mock_api.get_problem_info.return_value = {'name': 'Redis Fallback Problem', 'timeLimit': 1000, 'memoryLimit': 256}
        mock_api.download_and_extract_package.return_value = '<div class="title">Fallback</div>'
        mock_api.get_test_cases_from_redis.return_value = None
        mock_api.get_all_test_cases.return_value = [
            {'index': 1, 'input': '1\n', 'output': '1\n', 'is_sample': False}
        ]
        mock_api._make_request.return_value = []

        client = Client()
        user = User.objects.create_user(
            email='tester@example.com',
            username='tester',
            password='password123',
            first_name='Test',
            last_name='User',
            contact_number='1234567890',
            college='Engineering',
            gender='MALE',
            is_staff=True
        )
        client.login(username='tester@example.com', password='password123')

        response = client.post(reverse('problems:index'), {
            'problem_id': '601',
            'migrate_to_db': '1',
            'difficulty': 'easy',
            'tags': ['adhoc', 'implementation']
        })
        self.assertEqual(Problem.objects.filter(polygon_id='601').count(), 1)
        mock_api.get_all_test_cases.assert_called_with('601')
