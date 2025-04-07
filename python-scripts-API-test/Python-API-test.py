import os
import time
import threading
import queue
import logging
from dotenv import load_dotenv
from openstack import connection
from openstack.exceptions import ResourceTimeout, BadRequestException, ConflictException, ResourceNotFound
import dash
from dash import dcc, html, Input, Output
import plotly.graph_objs as go
from collections import defaultdict

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

def get_openstack_connection():
    """Establish OpenStack connection with service discovery"""
    try:
        logger.info("Initializing OpenStack connection")
        conn = connection.Connection(
            auth_url=os.getenv("OS_AUTH_URL"),
            project_name=os.getenv("OS_PROJECT_NAME"),
            username=os.getenv("OS_USERNAME"),
            password=os.getenv("OS_PASSWORD"),
            user_domain_name=os.getenv("OS_USER_DOMAIN_NAME", "Default"),
            project_domain_name=os.getenv("OS_PROJECT_DOMAIN_NAME", "Default"),
            region_name=os.getenv("OS_REGION_NAME", "region1"),
            interface=os.getenv("OS_INTERFACE", "public"),
            identity_api_version=os.getenv("OS_IDENTITY_API_VERSION", "3")
        )
        return conn
    except Exception as e:
        logger.error(f"Connection failed: {str(e)}")
        raise

# Performance metrics storage
api_metrics = {
    "latencies": defaultdict(list),  # {minute: [latencies]}
    "response_times": defaultdict(list),
    "throughput": defaultdict(list),
    "bandwidth": defaultdict(list),
    "success_count": 0,
    "failure_count": 0,
    "timestamps": []  # List of minute timestamps (e.g., "15:02")
}
vm_metrics = {
    "creation_times": [],
    "timestamps": []
}
vm_count = 0
metrics_queue = queue.Queue()
resource_lock = threading.Lock()

def measure_api_performance(func, *args, **kwargs):
    """Wrapper to measure API call performance"""
    start_time = time.time()
    try:
        result = func(*args, **kwargs)
        end_time = time.time()
        
        latency = (end_time - start_time) * 1000  # ms
        response_time = latency
        duration = end_time - start_time
        throughput = 1 / duration if duration > 0 else 0
        bandwidth = throughput * 1024  # Simulated bandwidth (bytes/sec)
        
        # Get minute timestamp (HH:MM)
        minute_timestamp = time.strftime("%H:%M", time.localtime(start_time))
        
        metrics_queue.put({
            "minute": minute_timestamp,
            "latency": latency,
            "response_time": response_time,
            "throughput": throughput,
            "bandwidth": bandwidth,
            "success": True
        })
        return result
    except Exception as e:
        end_time = time.time()
        minute_timestamp = time.strftime("%H:%M", time.localtime(start_time))
        metrics_queue.put({
            "minute": minute_timestamp,
            "latency": (end_time - start_time) * 1000,
            "success": False
        })
        logger.error(f"API call failed: {str(e)}")
        raise

def collect_metrics():
    """Background thread to collect performance metrics"""
    while True:
        try:
            metric = metrics_queue.get(timeout=1)
            with resource_lock:
                minute = metric["minute"]
                if minute not in api_metrics["timestamps"]:
                    api_metrics["timestamps"].append(minute)
                if metric["success"]:
                    api_metrics["latencies"][minute].append(metric["latency"])
                    api_metrics["response_times"][minute].append(metric["response_time"])
                    api_metrics["throughput"][minute].append(metric["throughput"])
                    api_metrics["bandwidth"][minute].append(metric["bandwidth"])
                    api_metrics["success_count"] += 1
                else:
                    api_metrics["failure_count"] += 1
            metrics_queue.task_done()
        except queue.Empty:
            continue

def pre_cleanup(conn):
    """Clean up any existing test resources"""
    try:
        logger.info("Checking for existing test resources")
        for network in conn.network.networks(name="test_network"):
            logger.info(f"Deleting network {network.name}")
            for subnet in conn.network.subnets(network_id=network.id):
                logger.info(f"Deleting subnet {subnet.name}")
                measure_api_performance(conn.network.delete_subnet, subnet)
            measure_api_performance(conn.network.delete_network, network)
        for sec_group in conn.network.security_groups(name="test_sec_group"):
            logger.info(f"Deleting security group {sec_group.name}")
            measure_api_performance(conn.network.delete_security_group, sec_group)
    except Exception as e:
        logger.error(f"Cleanup error: {str(e)}")
        raise

def create_network_resources(conn):
    """Create test network and subnet"""
    try:
        with resource_lock:
            logger.info("Creating test network")
            network = measure_api_performance(
                conn.network.create_network,
                name="test_network",
                admin_state_up=True
            )
            logger.info("Creating test subnet")
            subnet = measure_api_performance(
                conn.network.create_subnet,
                name="test_subnet",
                network_id=network.id,
                ip_version=4,
                cidr="192.168.1.0/24",
                gateway_ip="192.168.1.1",
                enable_dhcp=True
            )
            return network, subnet
    except Exception as e:
        logger.error(f"Network creation failed: {str(e)}")
        raise

def create_security_group(conn):
    """Create test security group with rules"""
    try:
        with resource_lock:
            logger.info("Creating security group")
            sec_group = measure_api_performance(
                conn.network.create_security_group,
                name="test_sec_group",
                description="Test security group"
            )
            measure_api_performance(
                conn.network.create_security_group_rule,
                security_group_id=sec_group.id,
                direction="ingress",
                protocol="tcp",
                port_range_min=22,
                port_range_max=22,
                remote_ip_prefix="0.0.0.0/0"
            )
            measure_api_performance(
                conn.network.create_security_group_rule,
                security_group_id=sec_group.id,
                direction="ingress",
                protocol="icmp",
                remote_ip_prefix="0.0.0.0/0"
            )
            return sec_group
    except Exception as e:
        logger.error(f"Security group creation failed: {str(e)}")
        raise

def find_image(conn):
    """Find suitable image for testing"""
    try:
        logger.info("Searching for available images")
        images = list(conn.image.images())
        if not images:
            raise Exception("No images available in the cloud")
        for img in images:
            if "cirros" in img.name.lower():
                logger.info(f"Found CirrOS image: {img.name}")
                return img
        logger.info(f"Using first available image: {images[0].name}")
        return images[0]
    except Exception as e:
        logger.error(f"Image search failed: {str(e)}")
        raise

def find_flavor(conn):
    """Find suitable flavor for testing"""
    try:
        logger.info("Searching for available flavors")
        flavors = list(conn.compute.flavors())
        if not flavors:
            raise Exception("No flavors available in the cloud")
        for flv in flavors:
            if flv.name == "m1.tiny":
                logger.info("Found m1.tiny flavor")
                return flv
        smallest = min(flavors, key=lambda x: x.ram)
        logger.info(f"Using smallest available flavor: {smallest.name} (RAM: {smallest.ram}MB)")
        return smallest
    except Exception as e:
        logger.error(f"Flavor search failed: {str(e)}")
        raise

def manage_vm_lifecycle(conn, network, sec_group):
    """Complete VM lifecycle management"""
    global vm_count, vm_metrics
    
    vm_name = f"test_vm_{vm_count}"
    
    try:
        image = find_image(conn)
        flavor = find_flavor(conn)
        
        logger.info(f"Starting VM {vm_name} lifecycle")
        start_time = time.time()
        
        server = measure_api_performance(
            conn.compute.create_server,
            name=vm_name,
            image_id=image.id,
            flavor_id=flavor.id,
            networks=[{"uuid": network.id}],
            security_groups=[{"name": sec_group.name}],
        )
        
        measure_api_performance(
            conn.compute.wait_for_server,
            server,
            status="ACTIVE",
            wait=300
        )
        
        creation_time = time.time() - start_time
        with resource_lock:
            vm_metrics["creation_times"].append(creation_time)
            vm_metrics["timestamps"].append(time.strftime("%H:%M:%S", time.localtime()))
            vm_count += 1
        
        logger.info(f"VM {vm_name} created in {creation_time:.2f}s")
        
        measure_api_performance(conn.compute.stop_server, server)
        measure_api_performance(
            conn.compute.wait_for_server,
            server,
            status="SHUTOFF",
            wait=300
        )
        
        measure_api_performance(conn.compute.start_server, server)
        measure_api_performance(
            conn.compute.wait_for_server,
            server,
            status="ACTIVE",
            wait=300
        )
        
        measure_api_performance(conn.compute.delete_server, server)
        measure_api_performance(
            conn.compute.wait_for_delete,
            server,
            wait=300
        )
        
        logger.info(f"Completed VM {vm_name} lifecycle")
        
    except Exception as e:
        logger.error(f"VM {vm_name} lifecycle failed: {str(e)}")
        raise

def cleanup_resources(conn, network, subnet, sec_group):
    """Clean up all test resources"""
    try:
        with resource_lock:
            logger.info("Cleaning up test resources")
            if subnet:
                measure_api_performance(conn.network.delete_subnet, subnet)
            if network:
                measure_api_performance(conn.network.delete_network, network)
            if sec_group:
                measure_api_performance(conn.network.delete_security_group, sec_group)
    except Exception as e:
        logger.error(f"Cleanup failed: {str(e)}")
        raise

# Dash application setup
app = dash.Dash(__name__)

app.layout = html.Div([
    html.H1("OpenStack Performance Test Dashboard", style={'textAlign': 'center'}),
    dcc.Interval(id="update-interval", interval=5000, n_intervals=0),
    
    html.Div([
        html.H3("Test Summary", style={'textAlign': 'center'}),
        html.Div(id="test-summary", style={'padding': '10px'}),
        html.Div(id="vm-summary", style={'padding': '10px'}),
        html.Div(id="error-summary", style={'padding': '10px'})
    ], style={'margin': '20px'}),
    
    html.Div([
        dcc.Graph(id="latency-graph"),
        dcc.Graph(id="response-time-graph"),
        dcc.Graph(id="throughput-graph"),
        dcc.Graph(id="bandwidth-graph"),
        dcc.Graph(id="api-success-failure-pie"),
        dcc.Graph(id="vm-creation-bar")
    ], style={'display': 'flex', 'flexWrap': 'wrap', 'justifyContent': 'space-around'})
])

@app.callback(
    [
        Output("test-summary", "children"),
        Output("vm-summary", "children"),
        Output("error-summary", "children"),
        Output("latency-graph", "figure"),
        Output("response-time-graph", "figure"),
        Output("throughput-graph", "figure"),
        Output("bandwidth-graph", "figure"),
        Output("api-success-failure-pie", "figure"),
        Output("vm-creation-bar", "figure")
    ],
    [Input("update-interval", "n_intervals")]
)
def update_dashboard(n):
    with resource_lock:
        # Aggregate metrics per minute
        avg_latencies = [sum(api_metrics["latencies"][minute]) / len(api_metrics["latencies"][minute]) if api_metrics["latencies"][minute] else 0 for minute in api_metrics["timestamps"]]
        avg_response_times = [sum(api_metrics["response_times"][minute]) / len(api_metrics["response_times"][minute]) if api_metrics["response_times"][minute] else 0 for minute in api_metrics["timestamps"]]
        avg_throughput = [sum(api_metrics["throughput"][minute]) / len(api_metrics["throughput"][minute]) if api_metrics["throughput"][minute] else 0 for minute in api_metrics["timestamps"]]
        avg_bandwidth = [sum(api_metrics["bandwidth"][minute]) / len(api_metrics["bandwidth"][minute]) if api_metrics["bandwidth"][minute] else 0 for minute in api_metrics["timestamps"]]
        
        # Summary metrics
        overall_avg_latency = sum(avg_latencies) / len(avg_latencies) if avg_latencies else 0
        overall_avg_throughput = sum(avg_throughput) / len(avg_throughput) if avg_throughput else 0
        total_calls = api_metrics["success_count"] + api_metrics["failure_count"]
        success_rate = (api_metrics["success_count"] / total_calls * 100) if total_calls > 0 else 0
        failure_rate = (api_metrics["failure_count"] / total_calls * 100) if total_calls > 0 else 0
        
        test_summary = html.Div([
            html.P(f"Test Duration: {time.strftime('%H:%M:%S', time.gmtime(n*5))}"),
            html.P(f"Average API Latency: {overall_avg_latency:.2f} ms"),
            html.P(f"Average Throughput: {overall_avg_throughput:.2f} req/sec")
        ])
        
        vm_summary = html.Div([
            html.P(f"VMs Created: {vm_count}"),
            html.P(f"Average Creation Time: {sum(vm_metrics['creation_times'])/len(vm_metrics['creation_times']):.2f}s" if vm_metrics["creation_times"] else "N/A")
        ])
        
        error_summary = html.Div([
            html.P(f"API Errors: {api_metrics['failure_count']}",
                   style={'color': 'red' if api_metrics['failure_count'] else 'green'})
        ])
        
        # Latency Graph (per minute)
        latency_fig = {
            'data': [go.Scatter(x=api_metrics["timestamps"], y=avg_latencies, mode='lines+markers', name='Avg Latency')],
            'layout': go.Layout(
                title='Average API Latency Per Minute',
                xaxis={'title': 'Time (HH:MM)', 'tickangle': -45},
                yaxis={'title': 'Latency (ms)'},
                hovermode='closest'
            )
        }
        
        # Response Time Graph (per minute)
        response_fig = {
            'data': [go.Scatter(x=api_metrics["timestamps"], y=avg_response_times, mode='lines+markers', name='Avg Response Time')],
            'layout': go.Layout(
                title='Average API Response Time Per Minute',
                xaxis={'title': 'Time (HH:MM)', 'tickangle': -45},
                yaxis={'title': 'Response Time (ms)'},
                hovermode='closest'
            )
        }
        
        # Throughput Graph (per minute)
        throughput_fig = {
            'data': [go.Scatter(x=api_metrics["timestamps"], y=avg_throughput, mode='lines+markers', name='Avg Throughput')],
            'layout': go.Layout(
                title='Average API Throughput Per Minute',
                xaxis={'title': 'Time (HH:MM)', 'tickangle': -45},
                yaxis={'title': 'Throughput (requests/sec)'},
                hovermode='closest'
            )
        }
        
        # Bandwidth Graph (per minute)
        bandwidth_fig = {
            'data': [go.Scatter(x=api_metrics["timestamps"], y=avg_bandwidth, mode='lines+markers', name='Avg Bandwidth')],
            'layout': go.Layout(
                title='Average Simulated Bandwidth Per Minute',
                xaxis={'title': 'Time (HH:MM)', 'tickangle': -45},
                yaxis={'title': 'Bandwidth (bytes/sec)'},
                hovermode='closest'
            )
        }
        
        # API Success/Failure Pie Chart
        api_pie_fig = {
            'data': [go.Pie(
                labels=['Success', 'Failure'],
                values=[api_metrics["success_count"], api_metrics["failure_count"]],
                textinfo='label+percent',
                hoverinfo='label+value'
            )],
            'layout': go.Layout(
                title='API Call Success vs Failure Rate'
            )
        }
        
        # VM Creation Bar Graph
        vm_bar_fig = {
            'data': [go.Bar(
                x=vm_metrics["timestamps"],
                y=vm_metrics["creation_times"],
                name='VM Creation Time',
                text=[f"VM {i+1}" for i in range(len(vm_metrics["creation_times"]))],
                hoverinfo='text+y'
            )],
            'layout': go.Layout(
                title='VM Creation Times Over Test Duration',
                xaxis={'title': 'Creation Time (HH:MM:SS)', 'tickangle': -45},
                yaxis={'title': 'Creation Time (seconds)'},
                bargap=0.2
            )
        }
        
        return (
            test_summary,
            vm_summary,
            error_summary,
            latency_fig,
            response_fig,
            throughput_fig,
            bandwidth_fig,
            api_pie_fig,
            vm_bar_fig
        )

def run_test_sequence():
    """Main test sequence"""
    conn = None
    network = None
    subnet = None
    sec_group = None
    
    try:
        conn = get_openstack_connection()
        metrics_thread = threading.Thread(target=collect_metrics, daemon=True)
        metrics_thread.start()
        
        pre_cleanup(conn)
        network, subnet = create_network_resources(conn)
        sec_group = create_security_group(conn)
        
        logger.info("Starting test sequence (10 minutes)")
        start_time = time.time()
        while time.time() - start_time < 600:  # 10 minutes
            manage_vm_lifecycle(conn, network, sec_group)
            time.sleep(5)
            
        logger.info("Test sequence completed successfully")
        
    except Exception as e:
        logger.error(f"Test sequence failed: {str(e)}")
    finally:
        if conn:
            cleanup_resources(conn, network, subnet, sec_group)

if __name__ == "__main__":
    test_thread = threading.Thread(target=run_test_sequence, daemon=True)
    test_thread.start()
    app.run(host="0.0.0.0", port=8050, debug=False)
