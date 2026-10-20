terraform {
  required_version = ">= 1.5.0"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
}

data "azurerm_resource_group" "assigned" {
  name = var.resource_group_name
}

data "azurerm_service_plan" "assigned" {
  name                = element(split("/", var.app_service_plan_id), length(split("/", var.app_service_plan_id)) - 1)
  resource_group_name = data.azurerm_resource_group.assigned.name
}

resource "azurerm_linux_web_app" "syncshield" {
  name                = var.web_app_name
  location            = var.location
  resource_group_name = data.azurerm_resource_group.assigned.name
  service_plan_id     = data.azurerm_service_plan.assigned.id
  https_only          = true

  site_config {
    always_on         = true
    app_command_line  = "python server.py"
    minimum_tls_version = "1.2"
    application_stack {
      python_version = "3.12"
    }
  }

  app_settings = {
    WEBSITES_PORT                      = "8000"
    WEBSITES_ENABLE_APP_SERVICE_STORAGE = "true"
    SYNCSHIELD_DB                      = "/home/data/syncshield.db"
    SCM_DO_BUILD_DURING_DEPLOYMENT     = "false"
  }

  identity {
    type = "SystemAssigned"
  }
}
