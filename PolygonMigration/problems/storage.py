from abc import ABC, abstractmethod
import os
import shutil
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

class BaseStorageService(ABC):
    @abstractmethod
    def upload_test_case(self, problem_id, test_number, input_data, output_data):
        pass

    @abstractmethod
    def delete_test_cases(self, problem_id):
        pass

    @abstractmethod
    def upload_file(self, blob_path, content):
        pass

    @abstractmethod
    def list_files(self, problem_id):
        pass


class LocalStorageService(BaseStorageService):
    def __init__(self, base_dir=None):
        self.base_dir = base_dir or getattr(settings, 'STORAGE_LOCAL_DIR', os.path.join(settings.BASE_DIR, 'test_cases'))
        os.makedirs(self.base_dir, exist_ok=True)

    def upload_test_case(self, problem_id, test_number, input_data, output_data):
        problem_dir = os.path.join(self.base_dir, str(problem_id))
        os.makedirs(problem_dir, exist_ok=True)
        in_path = os.path.join(problem_dir, str(test_number))
        out_path = os.path.join(problem_dir, f"{test_number}.a")

        in_bytes = input_data.encode('utf-8') if isinstance(input_data, str) else input_data
        out_bytes = output_data.encode('utf-8') if isinstance(output_data, str) else output_data

        with open(in_path, 'wb') as f:
            f.write(in_bytes)
        with open(out_path, 'wb') as f:
            f.write(out_bytes)

    def delete_test_cases(self, problem_id):
        problem_dir = os.path.join(self.base_dir, str(problem_id))
        if os.path.exists(problem_dir):
            shutil.rmtree(problem_dir)

    def upload_file(self, blob_path, content):
        full_path = os.path.join(self.base_dir, blob_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        data = content if isinstance(content, bytes) else content.encode('utf-8')
        with open(full_path, 'wb') as f:
            f.write(data)

    def list_files(self, problem_id):
        problem_dir = os.path.join(self.base_dir, str(problem_id))
        if not os.path.exists(problem_dir):
            return []
        files = []
        for root, _, filenames in os.walk(problem_dir):
            for name in filenames:
                rel = os.path.relpath(os.path.join(root, name), self.base_dir)
                files.append(rel.replace('\\', '/'))
        return files


class AzureStorageService(BaseStorageService):
    def __init__(self, connection_string=None, account_url=None, tenant_id=None, client_id=None, username=None, password=None, container_name=None):
        self.connection_string = connection_string or getattr(settings, 'AZURE_STORAGE_CONNECTION_STRING', os.getenv('AZURE_STORAGE_CONNECTION_STRING', ''))
        self.account_url = account_url or getattr(settings, 'AZURE_STORAGE_ACCOUNT_URL', os.getenv('AZURE_STORAGE_ACCOUNT_URL', ''))
        self.tenant_id = tenant_id or getattr(settings, 'AZURE_TENANT_ID', os.getenv('AZURE_TENANT_ID', ''))
        self.client_id = client_id or getattr(settings, 'AZURE_CLIENT_ID', os.getenv('AZURE_CLIENT_ID', ''))
        self.username = username or getattr(settings, 'AZURE_USERNAME', os.getenv('AZURE_USERNAME', ''))
        self.password = password or getattr(settings, 'AZURE_PASSWORD', os.getenv('AZURE_PASSWORD', ''))
        self.container_name = container_name or getattr(settings, 'AZURE_CONTAINER_NAME', os.getenv('AZURE_CONTAINER_NAME', 'testcases'))
        self._blob_service_client = None

    def _get_client(self):
        if self._blob_service_client is None:
            from azure.storage.blob import BlobServiceClient

            if self.connection_string:
                self._blob_service_client = BlobServiceClient.from_connection_string(self.connection_string)
            elif all([self.account_url, self.tenant_id, self.client_id, self.username, self.password]):
                if 'test-tenant' in self.tenant_id.lower() or 'your-tenant' in self.tenant_id.lower():
                    raise ValueError("Azure credentials in .env are placeholder values. Set real credentials or set STORAGE_PROVIDER=local.")
                from azure.identity import UsernamePasswordCredential
                credential = UsernamePasswordCredential(
                    tenant_id=self.tenant_id,
                    client_id=self.client_id,
                    username=self.username,
                    password=self.password,
                )
                self._blob_service_client = BlobServiceClient(
                    account_url=self.account_url,
                    credential=credential,
                )
            else:
                raise ValueError("Azure storage credentials are not properly configured. Provide AZURE_STORAGE_CONNECTION_STRING or Azure AD credentials.")
        return self._blob_service_client

    def upload_test_case(self, problem_id, test_number, input_data, output_data):
        input_blob_name = f"test_cases/{problem_id}/{test_number}"
        output_blob_name = f"test_cases/{problem_id}/{test_number}.a"
        client = self._get_client()

        input_client = client.get_blob_client(container=self.container_name, blob=input_blob_name)
        input_bytes = input_data.encode('utf-8') if isinstance(input_data, str) else input_data
        input_client.upload_blob(input_bytes, overwrite=True)

        output_client = client.get_blob_client(container=self.container_name, blob=output_blob_name)
        output_bytes = output_data.encode('utf-8') if isinstance(output_data, str) else output_data
        output_client.upload_blob(output_bytes, overwrite=True)

    def delete_test_cases(self, problem_id):
        client = self._get_client()
        container_client = client.get_container_client(self.container_name)
        prefix = f"test_cases/{problem_id}/"
        blobs_to_delete = [blob.name for blob in container_client.list_blobs(name_starts_with=prefix)]
        for blob_name in blobs_to_delete:
            container_client.delete_blob(blob_name)

    def upload_file(self, blob_path, content):
        client = self._get_client()
        blob_client = client.get_blob_client(container=self.container_name, blob=blob_path)
        data = content if isinstance(content, bytes) else content.encode('utf-8')
        blob_client.upload_blob(data, overwrite=True)

    def list_files(self, problem_id):
        client = self._get_client()
        container_client = client.get_container_client(self.container_name)
        prefix = f"test_cases/{problem_id}/"
        return [blob.name for blob in container_client.list_blobs(name_starts_with=prefix)]


def get_storage_service():
    provider = getattr(settings, 'STORAGE_PROVIDER', os.getenv('STORAGE_PROVIDER', '')).strip().lower()
    if provider == 'azure':
        return AzureStorageService()
    if provider == 'local':
        return LocalStorageService()

    conn_str = getattr(settings, 'AZURE_STORAGE_CONNECTION_STRING', os.getenv('AZURE_STORAGE_CONNECTION_STRING', ''))
    tenant_id = getattr(settings, 'AZURE_TENANT_ID', os.getenv('AZURE_TENANT_ID', ''))
    if conn_str or (tenant_id and 'test' not in tenant_id.lower() and 'your' not in tenant_id.lower()):
        return AzureStorageService()
    return LocalStorageService()

