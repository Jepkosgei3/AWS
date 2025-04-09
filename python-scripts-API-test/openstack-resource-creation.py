import os
import logging
import openstack
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OpenStackResourceCreator:
    def __init__(self):
        """Initialize OpenStack connection"""
        load_dotenv()
        self.conn = openstack.connect()
        logger.info("Successfully connected to OpenStack")

    def get_resource(self, resource_type, name):
        """Check if a resource exists"""
        try:
            resource = list(getattr(self.conn, resource_type).find(name=name))
            return resource[0] if resource else None
        except Exception:
            return None

    def create_or_update_resource(self, resource_type, create_func, **kwargs):
        """Check if a resource exists, and recreate it if needed"""
        name = kwargs.get("name")
        resource = self.get_resource(resource_type, name)
        if resource:
            logger.info(f"{resource_type} {name} already exists. Checking status...")
            if hasattr(resource, "status") and resource.status.lower() != "active":
                logger.info(f"{resource_type} {name} is not active. Deleting and recreating.")
                getattr(self.conn, resource_type).delete(resource)
                logger.info(f"{resource_type} {name} deleted. Creating a fresh one...")
                return create_func()
            logger.info(f"{resource_type} {name} is active. Proceeding to next resource.")
            return resource
        logger.info(f"Creating {resource_type} {name}...")
        try:
            resource = create_func()
            logger.info(f"{resource_type} {name} created successfully!")
            return resource
        except openstack.exceptions.ConflictException as e:
            if "Quota exceeded for resources: ['router']" in str(e):
                logger.warning("Router quota exceeded. Checking for existing routers...")
                # Try to find any existing router that might work
                existing_routers = list(self.conn.network.routers())
                if existing_routers:
                    logger.warning(f"Using existing router {existing_routers[0].name} as fallback")
                    return existing_routers[0]
                raise Exception("No routers available and quota exceeded - cannot proceed")
            raise

    def create_cluster_blueprint(self):
        """Create cluster blueprint"""
        blueprint_name = "test-blueprint"
        return self.create_or_update_resource("network", lambda: self.conn.network.create_network(
            name=blueprint_name
        ), name=blueprint_name)

    def create_physical_network(self):
        """Create physical network with subnet"""
        net_name = "phys-net-1"
        subnet_name = "phys-subnet-1"
        cidr = "192.168.1.0/24"
        
        network = self.create_or_update_resource("network", lambda: self.conn.network.create_network(
            name=net_name,
            provider_network_type='vlan',
            **{"router:external": True}
        ), name=net_name)
        
        # Create subnet if it doesn't exist
        subnet = self.get_resource("subnet", subnet_name)
        if not subnet:
            subnet = self.conn.network.create_subnet(
                name=subnet_name,
                network_id=network.id,
                ip_version=4,
                cidr=cidr
            )
            logger.info(f"Subnet {subnet_name} created successfully!")
        
        return network

    def create_virtual_network(self):
        """Create virtual network with subnet"""
        net_name = "virt-net-1"
        subnet_name = "virt-subnet-1"
        cidr = "10.0.0.0/24"
        
        network = self.create_or_update_resource("network", lambda: self.conn.network.create_network(
            name=net_name
        ), name=net_name)
        
        # Create subnet if it doesn't exist
        subnet = self.get_resource("subnet", subnet_name)
        if not subnet:
            subnet = self.conn.network.create_subnet(
                name=subnet_name,
                network_id=network.id,
                ip_version=4,
                cidr=cidr
            )
            logger.info(f"Subnet {subnet_name} created successfully!")
        
        return network

    def create_router(self, physical_net, virtual_net):
        """Create router and attach interfaces"""
        router_name = "test-router"
        
        # First try to get existing router
        router = self.get_resource("router", router_name)
        if router:
            logger.info(f"Using existing router {router_name}")
        else:
            logger.info(f"Attempting to create router {router_name}")
            try:
                router = self.conn.network.create_router(
                    name=router_name,
                    external_gateway_info={"network_id": physical_net.id}
                )
                logger.info(f"Router {router_name} created successfully!")
            except openstack.exceptions.ConflictException as e:
                if "Quota exceeded for resources: ['router']" in str(e):
                    logger.warning("Router quota exceeded. Checking for existing routers...")
                    # Try to find any existing router that might work
                    existing_routers = list(self.conn.network.routers())
                    if existing_routers:
                        logger.warning(f"Using existing router {existing_routers[0].name} as fallback")
                        router = existing_routers[0]
                    else:
                        raise Exception("No routers available and quota exceeded - cannot proceed")
                else:
                    raise
        
        # Get the virtual network's subnet
        subnet = next((s for s in self.conn.network.subnets() 
                      if s.network_id == virtual_net.id), None)
        
        if subnet:
            logger.info(f"Adding interface to router {router.name}...")
            try:
                # Check if interface already exists
                ports = list(self.conn.network.ports(device_id=router.id))
                if not any(p for p in ports if p.fixed_ips and p.fixed_ips[0]['subnet_id'] == subnet.id):
                    self.conn.network.add_interface_to_router(
                        router.id,
                        subnet_id=subnet.id
                    )
                    logger.info(f"Interface added to router {router.name} successfully!")
                else:
                    logger.info(f"Interface already exists on router {router.name}")
            except Exception as e:
                logger.warning(f"Could not add interface to router: {str(e)}")
        else:
            logger.warning("No subnet found for virtual network - skipping interface addition")
        
        return router

    def create_image(self):
        """Create an image with duplicate handling"""
        image_name = "test-image"
        image_path = "ubuntu-22.04-minimal-cloudimg-amd64.img"

        # First check for existing images
        existing_images = list(self.conn.image.images(name=image_name))
        if existing_images:
            logger.info(f"Found {len(existing_images)} existing images with name {image_name}")
            
            # If we have exactly one existing image, use it
            if len(existing_images) == 1:
                logger.info(f"Using existing image {existing_images[0].id}")
                return existing_images[0]
            
            # If multiple exist, delete all but the most recent
            logger.warning("Multiple images found - cleaning up duplicates")
            existing_images.sort(key=lambda x: x.created_at, reverse=True)
            
            # Keep the most recent image
            keep_image = existing_images[0]
            for img in existing_images[1:]:
                logger.info(f"Deleting duplicate image {img.id}")
                self.conn.image.delete_image(img)
            
            return keep_image

        logger.info(f"Creating new image {image_name}...")
        try:
            # Create image with all required parameters
            image = self.conn.image.create_image(
                name=image_name,
                disk_format='qcow2',
                container_format='bare',
                visibility='public'
            )
            
            logger.info(f"Uploading image data for {image_name}...")
            with open(image_path, "rb") as image_file:
                self.conn.image.upload_image(
                    image,
                    data=image_file,
                    disk_format='qcow2',
                    container_format='bare'
                )
            
            logger.info(f"Image {image_name} created successfully!")
            return image
        except Exception as e:
            logger.error(f"Failed to create image: {str(e)}")
            raise

    def create_flavor(self):
        """Create a flavor"""
        flavor_name = "m1.test"
        return self.create_or_update_resource("compute", lambda: self.conn.compute.create_flavor(
            name=flavor_name,
            ram=512,
            vcpus=1,
            disk=5
        ), name=flavor_name)

    def create_vm(self, image, flavor, network):
        """Create a virtual machine"""
        vm_name = "test-vm"
        return self.create_or_update_resource("compute", lambda: self.conn.compute.create_server(
            name=vm_name,
            image_id=image.id,
            flavor_id=flavor.id,
            networks=[{"uuid": network.id}]
        ), name=vm_name)

    def create_tenant(self):
        """Create a tenant"""
        tenant_name = "test-tenant"
        return self.create_or_update_resource("identity", lambda: self.conn.identity.create_project(
            name=tenant_name,
            domain_id='default'
        ), name=tenant_name)

    def main(self):
        try:
            # 1. Create cluster blueprint
            cluster_blueprint = self.create_cluster_blueprint()
            
            # 2. Create physical network with subnet
            physical_network = self.create_physical_network()
            
            # 3. Create virtual network with subnet
            virtual_network = self.create_virtual_network()
            
            # 4. Create router and connect networks
            router = self.create_router(physical_network, virtual_network)
            
            # 5. Create image (with duplicate handling)
            image = self.create_image()
            
            # 6. Create flavor
            flavor = self.create_flavor()
            
            # 7. Create VM
            vm = self.create_vm(image, flavor, virtual_network)
            
            # 8. Create tenant
            tenant = self.create_tenant()
            
            logger.info("All resources created successfully!")
            
        except Exception as e:
            logger.error(f"Resource creation failed: {str(e)}")
            raise

if __name__ == "__main__":
    creator = OpenStackResourceCreator()
    creator.main()