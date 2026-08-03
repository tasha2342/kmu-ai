import io
import boto3

from typing import Optional

from botocore.exceptions import ClientError

from app.config import config
from app.utils.logger import get_logger


logger = get_logger("s3", log_dir="logs")


class S3Manager:
    """S3 호환 스토리지 관리 클래스 (boto3 기반)"""

    def __init__(self):
        endpoint_url = f"{'https' if config.s3.secure else 'http'}://{config.s3.endpoint}"

        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=config.s3.access_key,
            aws_secret_access_key=config.s3.secret_key,
            region_name="us-east-1",
        )
        self.bucket_name = config.s3.bucket_name
        self._ensure_bucket_exists()

    def _ensure_bucket_exists(self):
        """버킷이 존재하지 않으면 생성합니다."""
        
        try:
            self.client.head_bucket(Bucket=self.bucket_name)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in ("404", "NoSuchBucket"):
                self.client.create_bucket(Bucket=self.bucket_name)
                logger.info(f"버킷이 생성되었습니다. (bucket={self.bucket_name})")
            else:
                logger.exception("버킷 확인 중 오류가 발생했습니다.")
                raise

    def upload_file(
        self,
        file_data: bytes,
        object_name: str,
        content_type: str = "application/octet-stream",
    ):
        """파일을 업로드합니다.
        
        Args:
            file_data (bytes): 업로드할 파일 데이터
            object_name (str): 저장될 객체 이름 (경로 포함)
            content_type (str): 파일의 Content-Type
        """
        
        self.client.put_object(
            Bucket=self.bucket_name,
            Key=object_name,
            Body=file_data,
            ContentType=content_type,
        )
        logger.debug(f"파일이 업로드되었습니다. (object={object_name})")

    def delete_file(self, object_name: str):
        """파일을 삭제합니다.
        
        Args:
            object_name (str): 삭제할 객체 이름 (경로 포함)
        """
        
        self.client.delete_object(Bucket=self.bucket_name, Key=object_name)
        logger.debug(f"파일이 삭제되었습니다. (object={object_name})")

    def file_exists(self, object_name: str) -> bool:
        """파일이 존재하는지 확인합니다.
        
        Args:
            object_name (str): 객체 이름 (경로 포함)
        
        Returns:
            bool: 파일 존재 여부
        """
        
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=object_name)
            return True
        except ClientError:
            return False

    def download_file(self, object_name: str) -> bytes:
        """파일을 다운로드합니다.
        
        Args:
            object_name (str): 다운로드할 객체 이름 (경로 포함)
        
        Returns:
            bytes: 다운로드한 파일 데이터
        """
        
        buf = io.BytesIO()
        self.client.download_fileobj(self.bucket_name, object_name, buf)
        buf.seek(0)
        return buf.read()

    def get_file_info(self, object_name: str) -> dict:
        """파일 정보를 조회합니다.
        
        Args:
            object_name (str): 객체 이름 (경로 포함)
        
        Returns:
            dict: 파일 정보 딕셔너리
        """
        
        response = self.client.head_object(
            Bucket=self.bucket_name,
            Key=object_name,
        )
        return {
            "size": response.get("ContentLength"),
            "content_type": response.get("ContentType"),
            "last_modified": response.get("LastModified"),
            "etag": response.get("ETag"),
            "metadata": response.get("Metadata", {}),
        }


_s3_manager: Optional[S3Manager] = None


def get_s3_manager() -> S3Manager:
    global _s3_manager
    if _s3_manager is None:
        _s3_manager = S3Manager()
    return _s3_manager
