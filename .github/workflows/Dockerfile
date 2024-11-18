# set the base image to use

FROM node:20-alpine

#optional but recommended to avoid running app as root to cripple the system
# for security, create a non-root user whose permissions is to only run the application
# -S creates a system user
# -G adds the user to a group

RUN addgroup app && adduser -S -G app app

# set user to run the application
USER app

# set our working directory to app
WORKDIR /app

# utilize dockers cache to saved deps by copying package.json and package-lock.json files to ./ first

COPY package*.json ./

# Ensure appropriate permissions available in cwd to run package files
# change user root and update permissions

USER root

# change ownership of /app directory to the app user
# chown -R <user>:<group> directory 
RUN chown -R app:app .

# change the user back to app user

#USER app

# install deps
RUN npm install

#copy the rest of the files to the working directory

COPY . .

# expose port to tell Docker that the container listens to on the specified network 5173
EXPOSE 5173

CMD npm run dev
# Use an official NGINX image
FROM nginx:alpine

# Copy the contents of your local directory to the NGINX directory
COPY ./webpage /usr/share/nginx/html

# Expose port 80 for web traffic
EXPOSE 80
