# Provider configuration
provider "aws" {
  region = "us-east-1" # Adjust region if needed
}

# VPC
resource "aws_vpc" "poc01_vpc" {
  cidr_block = "10.0.0.0/16"
  tags = {
    Name = "poc01-vpc"
    environment = "poc01"
  }
}
#Public Subnets
resource "aws_subnet" "poc01_public_subnet" {
  count       = 2
  vpc_id      = aws_vpc.poc01_vpc.id
  cidr_block  = cidrsubnet(aws_vpc.poc01_vpc.cidr_block, 8, count.index + 2)  # Adjust the CIDR block for the public subnet  availability_zone = element(data.aws_availability_zones.available.names, count.index)
  
  map_public_ip_on_launch = true  # Automatically assign public IPs on EC2 instance launch

  tags = {
    Name        = "poc01-public-subnet-${count.index + 1}"
    environment = "poc01"
  }
}

# Private Subnets
resource "aws_subnet" "poc01_private_subnet" {
  count = 2
  vpc_id = aws_vpc.poc01_vpc.id
  cidr_block = cidrsubnet(aws_vpc.poc01_vpc.cidr_block, 8, count.index)
  map_public_ip_on_launch = false
  tags = {
    Name = "poc01-private-subnet-${count.index + 1}"
    environment = "poc01"
  }
}

# Internet Gateway (CloudFront only)
resource "aws_internet_gateway" "poc01_igw" {
  vpc_id = aws_vpc.poc01_vpc.id

  tags = {
    Name        = "poc01-internet-gateway"
    environment = "poc01"
  }
}

# Route Table (Internet Gateway for CloudFront)
resource "aws_route_table" "poc01_public_route_table" {
  vpc_id = aws_vpc.poc01_vpc.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.poc01_igw.id
  }
  tags = {
    Name = "poc01-public-route-table"
    environment = "poc01"
  }
}

# Route Table Association for Subnets
resource "aws_route_table_association" "poc01_private_rt_assoc" {
  count          = 2
  subnet_id      = aws_subnet.poc01_private_subnet[count.index].id
  route_table_id = aws_route_table.poc01_public_route_table.id
}

# Security Group for EC2 (WebSocket and SSH)
resource "aws_security_group" "poc01_ec2_sg" {
  vpc_id = aws_vpc.poc01_vpc.id
  ingress {
    from_port = 80
    to_port = 80
    protocol = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port = 22
    to_port = 22
    protocol = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port = 0
    to_port = 0
    protocol = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = {
    Name = "poc01-ec2-sg"
    environment = "poc01"
  }
}

# EC2 Key Pair
resource "aws_key_pair" "poc01_key" {
  key_name = "poc01-ec2-key"
  public_key = file("~/.ssh/id_rsa.pub")
}

# EC2 Instance with WebSocket API
resource "aws_instance" "poc01_ec2" {
  ami = "ami-0e86e20dae9224db8" # Ubuntu AMI
  instance_type = "t2.micro"
  key_name = aws_key_pair.poc01_key.key_name
  subnet_id = aws_subnet.poc01_private_subnet[0].id
  vpc_security_group_ids = [aws_security_group.poc01_ec2_sg.id]
  
  # WebSocket API setup via User Data
  user_data = <<-EOF
    #!/bin/bash
    apt-get update
    apt-get install -y python3-pip
    pip3 install flask flask-socketio
    mkdir /opt/websocket-api
    cat <<'EOT' > /opt/websocket-api/app.py
    from flask import Flask
    from flask_socketio import SocketIO, send
    app = Flask(__name__)
    socketio = SocketIO(app)
    
    @socketio.on('message')
    def handle_message(msg):
      print(f'Message: {msg}')
      send(f'You said: {msg}', broadcast=True)
    
    if __name__ == '__main__':
      socketio.run(app, host='0.0.0.0', port=80)
    EOT
    python3 /opt/websocket-api/app.py &
  EOF
  
  tags = {
    Name = "poc01-ec2-instance"
    environment = "poc01"
  }
}

# Lambda IAM Role
resource "aws_iam_role" "poc01_lambda_role" {
  name = "poc01-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Action = "sts:AssumeRole",
      Effect = "Allow",
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
  tags = {
    Name = "poc01-lambda-role"
    environment = "poc01"
  }
}

# Lambda Role Policy (CloudWatch Logs)
resource "aws_iam_role_policy" "poc01_lambda_policy" {
  role = aws_iam_role.poc01_lambda_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Action = [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      Effect = "Allow",
      Resource = "*"
    }]
  })
}

# Lambda Function
resource "aws_lambda_function" "poc01_lambda" {
  function_name = "poc01-api-handler"
  role = aws_iam_role.poc01_lambda_role.arn
  runtime = "python3.9"
  handler = "lambda_function.lambda_handler"
  filename = "${path.module}/lambda_function.zip"
  source_code_hash = filebase64sha256("${path.module}/lambda_function.zip")
  environment {
    variables = {
      ENV = "poc01"
    }
  }
  tags = {
    Name = "poc01-lambda"
    environment = "poc01"
  }
}

# Lambda Permission for EC2 to invoke
resource "aws_lambda_permission" "poc01_lambda_permission" {
  statement_id  = "AllowEC2Invocation"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.poc01_lambda.function_name
  principal     = "ec2.amazonaws.com"
}

# S3 Bucket for Frontend
resource "aws_s3_bucket" "poc01_s3" {
  bucket = "poc01-frontend-bucket"

  tags = {
    Name        = "poc01-s3-bucket"
    environment = "poc01"
  }
}

resource "aws_s3_bucket_public_access_block" "poc01_s3_public_access" {
  bucket = aws_s3_bucket.poc01_s3.id

  block_public_acls       = true
  ignore_public_acls      = true
  block_public_policy     = false
  restrict_public_buckets = false
}

# CloudFront Distribution
resource "aws_cloudfront_distribution" "poc01_cloudfront" {
  origin {
    domain_name = aws_s3_bucket.poc01_s3.bucket_regional_domain_name
    origin_id   = "S3-poc01"
    s3_origin_config {
      origin_access_identity = aws_cloudfront_origin_access_identity.poc01_oai.cloudfront_access_identity_path
    }
  }

  enabled             = true
  is_ipv6_enabled      = true
  default_root_object  = "index.html"

  default_cache_behavior {
  allowed_methods  = ["GET", "HEAD"]
  cached_methods   = ["GET", "HEAD"]
  target_origin_id = "S3-poc01"

  forwarded_values {
    query_string = false

    cookies {
      forward = "none" # You can change this based on how you want to forward cookies
    }
  }

  viewer_protocol_policy = "redirect-to-https"
}
  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = {
    Name        = "poc01-cloudfront"
    environment = "poc01"
  }
}


# CloudFront Origin Access Identity
resource "aws_cloudfront_origin_access_identity" "poc01_oai" {
  comment = "Origin Access Identity for S3 Frontend Bucket"
}

# S3 Bucket Policy for CloudFront Access
resource "aws_s3_bucket_policy" "poc01_s3_policy" {
  bucket = aws_s3_bucket.poc01_s3.bucket
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Sid = "AllowCloudFrontAccess",
      Effect = "Allow",
      Principal = {
        AWS = aws_cloudfront_origin_access_identity.poc01_oai.iam_arn
      },
      Action = "s3:GetObject",
      Resource = "${aws_s3_bucket.poc01_s3.arn}/*"
    }]
  })
}

