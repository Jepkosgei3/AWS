import requests
import dash
from dash import dcc, html
import os
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

OS_AUTH_URL = os.getenv("OS_AUTH_URL")
OS_USERNAME = os.getenv("OS_USERNAME")
OS_PASSWORD = os.getenv("OS_PASSWORD")
OS_PROJECT_NAME = os.getenv("OS_PROJECT_NAME")
OS_PROJECT_DOMAIN_NAME = os.getenv("OS_PROJECT_DOMAIN_NAME")
OS_USER_DOMAIN_NAME = os.getenv("OS_USER_DOMAIN_NAME")

# Hardcoded known working endpoints
KNOWN_ENDPOINTS = {
    "image": "https://sa-demo-region2.app.qa-pcd.platform9.com/glance/v2/images",
    "network": "https://sa-demo-region1.app.qa-pcd.platform9.com/neutron/v2.0/networks",
    "loadbalancer": "https://sa-demo-region2.app.qa-pcd.platform9.com/octavia/v2.0/lbaas/loadbalancers",
    "subnet": "https://sa-demo-region2.app.qa-pcd.platform9.com/neutron/v2.0/subnets",
    "security_group": "https://sa-demo-region2.app.qa-pcd.platform9.com/neutron/v2.0/security-groups",
    "flavor": "https://sa-demo-region2.app.qa-pcd.platform9.com/nova/v2.1/flavors",
    "volume_type": "https://sa-demo-region2.app.qa-pcd.platform9.com/cinder/v3/types",
    "user": "https://sa-demo.app.qa-pcd.platform9.com/keystone/v3/users",
    "keypair": "https://sa-demo-region2.app.qa-pcd.platform9.com/nova/v2.1/os-keypairs",
    "port": "https://sa-demo-region2.app.qa-pcd.platform9.com/neutron/v2.0/ports",
    "host": "https://sa-demo-region2.app.qa-pcd.platform9.com/nova/v2.1/os-hosts",
    "volume_image_metadata": "https://sa-demo-region2.app.qa-pcd.platform9.com/cinder/v3/volumes",
    "server_group": "https://sa-demo-region2.app.qa-pcd.platform9.com/nova/v2.1/os-server-groups",
    "hypervisor": "https://sa-demo-region2.app.qa-pcd.platform9.com/nova/v2.1/os-hypervisors",
    "backend": "https://sa-demo-region2.app.qa-pcd.platform9.com/cinder/v3/backends",
    "project": "https://sa-demo.app.qa-pcd.platform9.com/keystone/v3/projects",
    "host_config": "https://sa-demo-region2.app.qa-pcd.platform9.com/nova/v2.1/os-host-configs",
    "cluster_blueprint": "https://sa-demo-region2.app.qa-pcd.platform9.com/cluster/v1/blueprints",
}

# Authenticate and get a token
def get_token():
    auth_data = {
        "auth": {
            "identity": {
                "methods": ["password"],
                "password": {
                    "user": {
                        "name": OS_USERNAME,
                        "domain": {"name": OS_USER_DOMAIN_NAME},
                        "password": OS_PASSWORD
                    }
                }
            },
            "scope": {
                "project": {
                    "name": OS_PROJECT_NAME,
                    "domain": {"name": OS_PROJECT_DOMAIN_NAME}
                }
            }
        }
    }
    response = requests.post(f"{OS_AUTH_URL}/auth/tokens", json=auth_data)
    if response.status_code == 201:
        print("✅ Authentication successful")
        return response.headers["X-Subject-Token"], response.json()  # Return full token response
    else:
        print(f"❌ Authentication failed: {response.status_code}, Response: {response.text}")
        raise Exception("Failed to authenticate with OpenStack")

# Extract the project ID from the token response
def get_project_id(token_response):
    """Extract the project ID from the token response."""
    try:
        project_id = token_response["token"]["project"]["id"]
        print(f"✅ Project ID: {project_id}")
        return project_id
    except KeyError:
        print("❌ Project ID not found in token response.")
        return None

# Extract endpoints dynamically
def get_service_endpoints(service_catalog):
    endpoints = {}
    for service in service_catalog:
        service_type = service["type"]
        for endpoint in service["endpoints"]:
            if endpoint["interface"] == "public":
                endpoints[service_type] = endpoint["url"].rstrip('/')
                break  # Stop after finding the first public endpoint
    return endpoints

# Function to fetch metadata
def fetch_metadata(endpoint, token):
    headers = {"X-Auth-Token": token}
    try:
        print(f"Fetching data from {endpoint}")
        response = requests.get(endpoint, headers=headers, timeout=10)
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print(f"Response Data: {json.dumps(data, indent=2)}")  # Debugging: Print response data
            return data
        elif response.status_code == 300:  # Handle Multiple Choices
            print(f"⚠️ Received 300 Multiple Choices for {endpoint}")
            choices = response.json().get("versions", [])
            if choices:
                # Find the preferred choice (usually "CURRENT" or highest version)
                for choice in choices:
                    if "status" in choice and choice["status"].lower() == "current":
                        new_endpoint = choice.get("links", [{}])[0].get("href", "")
                        if new_endpoint:
                            print(f"🔄 Retrying with: {new_endpoint}")
                            return fetch_metadata(new_endpoint, token)
            return {"error": "Multiple choices returned, but no valid option found", "response": response.text}
        elif response.status_code == 404:
            print(f"⚠️ Resource not found at {endpoint} (404). Skipping.")
            return {"error": "Resource not found"}
        else:
            print(f"⚠️ Failed with status {response.status_code}, Response: {response.text}")
            return {"error": f"Failed with status {response.status_code}", "response": response.text}
    except Exception as e:
        print(f"⚠️ Exception occurred: {str(e)}")
        return {"error": str(e)}

# Get authentication token and service catalog
token, token_response = get_token()
project_id = get_project_id(token_response)  # Extract project ID
service_catalog = token_response["token"]["catalog"]

# Debugging output to inspect the service catalog
service_endpoints = get_service_endpoints(service_catalog)

# Merge known endpoints with dynamically retrieved ones
all_endpoints = {**KNOWN_ENDPOINTS, **service_endpoints}
print("Image URL:", all_endpoints.get('image', ''))
print("Network URL:", all_endpoints.get('network', ''))

# Define metadata collection points
metadata_endpoints = {
    "compute_servers": f"{all_endpoints.get('compute', '')}/servers",
    "images": f"{all_endpoints.get('image', '')}/v2/images?owner={project_id}",  # Scope to project
    "networks": f"{all_endpoints.get('network', '')}/v2.0/networks?tenant_id={project_id}",  # Scope to project
    "subnets": f"{all_endpoints.get('network', '')}/v2.0/subnets?tenant_id={project_id}",  # Scope to project
    "security_groups": f"{all_endpoints.get('network', '')}/v2.0/security-groups?tenant_id={project_id}",  # Scope to project
    "flavors": f"{all_endpoints.get('compute', '')}/flavors",  # Flavors are global, no project scope
    "volume_types": f"{all_endpoints.get('volumev3', '')}/types",  # Volume types are global
    "users": f"{all_endpoints.get('identity', '')}/users",  # Users are global
    "keypairs": f"{all_endpoints.get('compute', '')}/os-keypairs",  # Keypairs are global
    "ports": f"{all_endpoints.get('network', '')}/v2.0/ports",  # Ports are global
    "hosts": f"{all_endpoints.get('compute', '')}/os-hosts",  # Hosts are global
    "volume_image_metadata": f"{all_endpoints.get('volumev3', '')}/volumes",  # Volume image metadata
    "server_groups": f"{all_endpoints.get('compute', '')}/os-server-groups",  # Server groups are global
    "hypervisors": f"{all_endpoints.get('compute', '')}/os-hypervisors",  # Hypervisors are global
    "backends": f"{all_endpoints.get('volumev3', '')}/backends",  # Backends are global
    "projects": f"{all_endpoints.get('identity', '')}/projects",  # Projects are global
    "host_configs": f"{all_endpoints.get('compute', '')}/os-host-configs",  # Host configs are global
    "cluster_blueprints": f"{all_endpoints.get('cluster', '')}/v1/blueprints",  # Cluster blueprints are global
}

# Fetch metadata from all services
metadata = {}

# Use hardcoded endpoints for specific resources if dynamic endpoints fail
images_endpoint = KNOWN_ENDPOINTS.get("image", "") + f"?owner={project_id}"
networks_endpoint = KNOWN_ENDPOINTS.get("network", "") + f"?tenant_id={project_id}"
subnets_endpoint = KNOWN_ENDPOINTS.get("subnet", "") + f"?tenant_id={project_id}"
security_groups_endpoint = KNOWN_ENDPOINTS.get("security_group", "") + f"?tenant_id={project_id}"
flavors_endpoint = KNOWN_ENDPOINTS.get("flavor", "")
volume_types_endpoint = KNOWN_ENDPOINTS.get("volume_type", "")
users_endpoint = KNOWN_ENDPOINTS.get("user", "")
keypairs_endpoint = KNOWN_ENDPOINTS.get("keypair", "")
ports_endpoint = KNOWN_ENDPOINTS.get("port", "")
hosts_endpoint = KNOWN_ENDPOINTS.get("host", "")
volume_image_metadata_endpoint = KNOWN_ENDPOINTS.get("volume_image_metadata", "")
server_groups_endpoint = KNOWN_ENDPOINTS.get("server_group", "")
hypervisors_endpoint = KNOWN_ENDPOINTS.get("hypervisor", "")
backends_endpoint = KNOWN_ENDPOINTS.get("backend", "")
projects_endpoint = KNOWN_ENDPOINTS.get("project", "")
host_configs_endpoint = KNOWN_ENDPOINTS.get("host_config", "")
cluster_blueprints_endpoint = KNOWN_ENDPOINTS.get("cluster_blueprint", "")

metadata["images"] = fetch_metadata(images_endpoint, token)
metadata["networks"] = fetch_metadata(networks_endpoint, token)
metadata["subnets"] = fetch_metadata(subnets_endpoint, token)
metadata["security_groups"] = fetch_metadata(security_groups_endpoint, token)
metadata["flavors"] = fetch_metadata(flavors_endpoint, token)
metadata["volume_types"] = fetch_metadata(volume_types_endpoint, token)
metadata["users"] = fetch_metadata(users_endpoint, token)
metadata["keypairs"] = fetch_metadata(keypairs_endpoint, token)
metadata["ports"] = fetch_metadata(ports_endpoint, token)
metadata["hosts"] = fetch_metadata(hosts_endpoint, token)
metadata["volume_image_metadata"] = fetch_metadata(volume_image_metadata_endpoint, token)
metadata["server_groups"] = fetch_metadata(server_groups_endpoint, token)
metadata["hypervisors"] = fetch_metadata(hypervisors_endpoint, token)
metadata["backends"] = fetch_metadata(backends_endpoint, token)
metadata["projects"] = fetch_metadata(projects_endpoint, token)
metadata["host_configs"] = fetch_metadata(host_configs_endpoint, token)
metadata["cluster_blueprints"] = fetch_metadata(cluster_blueprints_endpoint, token)

# Fetch metadata for other services
for service, url in metadata_endpoints.items():
    if service not in ["images", "networks", "subnets", "security_groups", "flavors", "volume_types", "users", "keypairs", "ports", "hosts", "volume_image_metadata", "server_groups", "hypervisors", "backends", "projects", "host_configs", "cluster_blueprints"] and url:
        metadata[service] = fetch_metadata(url, token)

print("Fetched Metadata:")
print("Images Metadata:", metadata.get("images", {}))
print("Networks Metadata:", metadata.get("networks", {}))
print("Subnets Metadata:", metadata.get("subnets", {}))
print("Security Groups Metadata:", metadata.get("security_groups", {}))
print("Flavors Metadata:", metadata.get("flavors", {}))
print("Volume Types Metadata:", metadata.get("volume_types", {}))
print("Users Metadata:", metadata.get("users", {}))
print("Keypairs Metadata:", metadata.get("keypairs", {}))
print("Ports Metadata:", metadata.get("ports", {}))
print("Hosts Metadata:", metadata.get("hosts", {}))
print("Volume Image Metadata:", metadata.get("volume_image_metadata", {}))
print("Server Groups Metadata:", metadata.get("server_groups", {}))
print("Hypervisors Metadata:", metadata.get("hypervisors", {}))
print("Backends Metadata:", metadata.get("backends", {}))
print("Projects Metadata:", metadata.get("projects", {}))
print("Host Configs Metadata:", metadata.get("host_configs", {}))
print("Cluster Blueprints Metadata:", metadata.get("cluster_blueprints", {}))

# Format metadata for better readability
def format_json(data):
    return json.dumps(data, indent=4, sort_keys=True)

# Dash App for Displaying Metadata
app = dash.Dash(__name__)

app.layout = html.Div([
    html.H1("OpenStack Services Metadata"),
    *[html.Div([
        html.H2(service.replace('_', ' ').title()),
        html.Pre(format_json(data))  # Pretty-print JSON data
    ]) for service, data in metadata.items() if data]  # Only display services with data
])

if __name__ == "__main__":
    app.run(debug=True)