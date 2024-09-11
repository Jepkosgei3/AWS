# Use an official NGINX image
FROM nginx:alpine

# Copy the contents of your local directory to the NGINX directory
COPY ./webpage /usr/share/nginx/html

# Expose port 80 for web traffic
EXPOSE 80
