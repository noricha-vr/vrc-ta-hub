import os
import sys
import boto3
from botocore.client import Config

def main():
    output_dir = '/bkp'
    
    # 環境変数はdocker-composeから渡される
    endpoint = os.environ.get('AWS_S3_ENDPOINT_URL')
    key_id = os.environ.get('AWS_ACCESS_KEY_ID')
    secret = os.environ.get('AWS_SECRET_ACCESS_KEY')
    bucket_name = os.environ.get('AWS_STORAGE_BUCKET_NAME')
    
    if not all([endpoint, key_id, secret, bucket_name]):
        print("Error: Missing AWS environment variables.")
        print(f"Endpoint: {endpoint}")
        print(f"Bucket: {bucket_name}")
        sys.exit(1)

    print(f"Connecting to R2 bucket: {bucket_name}")
    
    s3 = boto3.resource('s3',
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        config=Config(signature_version='s3v4')
    )
    
    bucket = s3.Bucket(bucket_name)
    
    count = 0
    for obj in bucket.objects.all():
        target_path = os.path.join(output_dir, obj.key)
        target_dir = os.path.dirname(target_path)
        
        if not os.path.exists(target_dir):
            os.makedirs(target_dir)
            
        print(f"Downloading {obj.key}...")
        bucket.download_file(obj.key, target_path)
        count += 1
        
    print(f"Download complete. {count} files downloaded.")

if __name__ == '__main__':
    main()