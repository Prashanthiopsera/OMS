#!/bin/bash
set -euo pipefail

echo "🚀 Deploying OMS Infrastructure to Kubernetes..."

# Ensure namespace exists
kubectl create namespace oms-marvell-dev --dry-run=client -o yaml | kubectl apply -f -

# Deploy databases
echo "📦 Deploying PostgreSQL, MongoDB, Redis, Elasticsearch..."
kubectl apply -f /deploy/kubernetes/databases.yaml

# Wait for databases to be ready
echo "⏳ Waiting for databases to be ready..."
kubectl wait --for=condition=ready pod -l app=postgres -n oms-marvell-dev --timeout=300s || echo "Postgres startup in progress..."
kubectl wait --for=condition=ready pod -l app=mongodb -n oms-marvell-dev --timeout=300s || echo "MongoDB startup in progress..."
kubectl wait --for=condition=ready pod -l app=redis -n oms-marvell-dev --timeout=300s || echo "Redis startup in progress..."

# Deploy ConfigMap and Secrets
echo "🔧 Deploying ConfigMap and Secrets..."
kubectl apply -f /deploy/kubernetes/configmap.yaml
kubectl apply -f /deploy/kubernetes/secrets.yaml

echo "✅ Infrastructure deployment complete!"
echo ""
echo "Database Services:"
echo "  - PostgreSQL: postgres.oms-marvell-dev.svc.cluster.local:5432"
echo "  - MongoDB: mongodb.oms-marvell-dev.svc.cluster.local:27017"
echo "  - Redis: redis.oms-marvell-dev.svc.cluster.local:6379"
echo "  - Elasticsearch: elasticsearch.oms-marvell-dev.svc.cluster.local:9200"
