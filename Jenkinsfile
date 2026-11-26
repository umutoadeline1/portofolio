pipeline {
    agent any

    environment {
        BACKEND_IMAGE = 'umutoni12/portfolio-backend:latest'
        FRONTEND_IMAGE = 'umutoni12/portfolio-frontend:latest'
        DOCKER_NETWORK = 'portfolio-network'
    }

    stages {
        stage('Pull Docker Hub images') {
            steps {
                withCredentials([
                    usernamePassword(
                        credentialsId: 'docker-registry-credentials',
                        usernameVariable: 'DOCKERHUB_USERNAME',
                        passwordVariable: 'DOCKERHUB_PASSWORD'
                    )
                ]) {
                    sh '''
                        set -eu
                        printf '%s' "$DOCKERHUB_PASSWORD" |
                            docker login --username "$DOCKERHUB_USERNAME" --password-stdin
                        trap 'docker logout >/dev/null 2>&1 || true' EXIT
                        docker pull "$BACKEND_IMAGE"
                        docker pull "$FRONTEND_IMAGE"
                    '''
                }
            }
        }

        stage('Deploy containers') {
            steps {
                withCredentials([
                    string(
                        credentialsId: 'portfolio-jwt-secret',
                        variable: 'JWT_SECRET_KEY'
                    )
                ]) {
                    sh '''
                        set -eu

                        if ! docker network inspect "$DOCKER_NETWORK" >/dev/null 2>&1; then
                            docker network create "$DOCKER_NETWORK"
                        fi

                        for container in portfolio-backend portfolio-frontend; do
                            if docker container inspect "$container" >/dev/null 2>&1; then
                                docker container rm --force "$container"
                            fi
                        done

                        docker volume create portfolio_data >/dev/null
                        docker volume create portfolio_uploads >/dev/null

                        set +x
                        docker run --detach \
                            --name portfolio-backend \
                            --network "$DOCKER_NETWORK" \
                            --network-alias backend \
                            --restart unless-stopped \
                            --publish 5000:5000 \
                            --env DATABASE=/app/data/portfolio.db \
                            --env UPLOAD_FOLDER=/app/uploads \
                            --env JWT_SECRET_KEY="$JWT_SECRET_KEY" \
                            --volume portfolio_data:/app/data \
                            --volume portfolio_uploads:/app/uploads \
                            "$BACKEND_IMAGE"
                        set -x

                        docker run --detach \
                            --name portfolio-frontend \
                            --network "$DOCKER_NETWORK" \
                            --restart unless-stopped \
                            --publish 8083:80 \
                            "$FRONTEND_IMAGE"
                    '''
                }
            }
        }

        stage('Verify deployment') {
            steps {
                sh '''
                    set -eu

                    for container in portfolio-backend portfolio-frontend; do
                        running="$(docker inspect --format '{{.State.Running}}' "$container")"
                        if [ "$running" != 'true' ]; then
                            docker logs "$container"
                            echo "Container $container is not running" >&2
                            exit 1
                        fi
                    done

                    verified=false
                    for attempt in $(seq 1 30); do
                        if docker exec portfolio-backend python -c \
                            "import urllib.request; urllib.request.urlopen('http://portfolio-frontend/', timeout=3).read(); urllib.request.urlopen('http://portfolio-frontend/api/health', timeout=3).read()" \
                            >/dev/null 2>&1; then
                            verified=true
                            break
                        fi
                        sleep 2
                    done

                    if [ "$verified" != 'true' ]; then
                        docker logs portfolio-backend
                        docker logs portfolio-frontend
                        echo 'Frontend or backend health verification failed' >&2
                        exit 1
                    fi

                    echo 'Both containers are running; frontend and proxied backend health checks passed.'
                '''
            }
        }
    }
}
