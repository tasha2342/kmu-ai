pipeline {
	agent any

	options {
		timeout(time: 30, unit: 'MINUTES')
		buildDiscarder(logRotator(numToKeepStr: '20'))
		disableConcurrentBuilds()
		timestamps()
	}

	environment {
		PROJECT         = 'backend'
		APP_NAME        = 'kmu-ai-api'
		DOCKER_REGISTRY = 'harbor.jdone.co.kr'
		BUILD_PLATFORM  = 'linux/amd64'
	}

	stages {
		stage('🔀 Checkout') {
			steps {
				echo "📥 소스코드를 체크아웃합니다. — Branch: ${env.BRANCH_NAME}"
				checkout scm
			}
		}

		stage('🏷️ Prepare Tags') {
			steps {
				script {
					env.BUILD_DATE    = sh(script: "date +%Y%m%d", returnStdout: true).trim()
					env.OCI_CREATED   = sh(script: "date -u +'%Y-%m-%dT%H:%M:%SZ'", returnStdout: true).trim()
					env.GIT_HASH      = sh(script: "git rev-parse --short HEAD", returnStdout: true).trim()
					env.GIT_HASH_FULL = sh(script: "git rev-parse HEAD", returnStdout: true).trim()
					env.GIT_MESSAGE   = sh(script: "git log -1 --pretty=%s", returnStdout: true).trim()
					env.GIT_URL       = sh(script: "git remote get-url origin", returnStdout: true).trim()
					env.IMAGE_TAG     = "${env.DOCKER_REGISTRY}/${env.PROJECT}/${env.APP_NAME}:${env.BUILD_DATE}-${env.GIT_HASH}"
					env.LATEST_TAG    = "${env.DOCKER_REGISTRY}/${env.PROJECT}/${env.APP_NAME}:latest"
					env.IS_MAIN       = (env.BRANCH_NAME == 'main' || env.BRANCH_NAME == 'master') ? 'true' : 'false'

					echo "🔖 IMAGE  : ${env.IMAGE_TAG}"
					echo "📝 COMMIT : ${env.GIT_HASH} — ${env.GIT_MESSAGE}"
				}
			}
		}

		stage('🔐 Harbor Login') {
			steps {
				withCredentials([usernamePassword(
					credentialsId: 'harbor-repo',
					usernameVariable: 'HARBOR_USERNAME',
					passwordVariable: 'HARBOR_PASSWORD'
				)]) {
					sh '''
						set -e
						echo "$HARBOR_PASSWORD" | docker login "${DOCKER_REGISTRY}" -u "$HARBOR_USERNAME" --password-stdin
					'''
				}
				echo "✅ Harbor 로그인 성공 — ${env.DOCKER_REGISTRY}"
			}
		}

		stage('🐳 Build & Push') {
			steps {
				script {
					def pushArgs = "-t \"${env.IMAGE_TAG}\""
					if (env.IS_MAIN == 'true') {
						pushArgs += " -t \"${env.LATEST_TAG}\""
						echo "🚀 main 브랜치 — latest 태그도 함께 push합니다."
					}
					sh """
						set -e
						docker buildx build \
							--platform    ${env.BUILD_PLATFORM} \
							--provenance  true \
							--pull \
							--label       "org.opencontainers.image.created=${env.OCI_CREATED}" \
							--label       "org.opencontainers.image.revision=${env.GIT_HASH_FULL}" \
							--label       "org.opencontainers.image.source=${env.GIT_URL}" \
							--label       "org.opencontainers.image.version=${env.BUILD_DATE}-${env.GIT_HASH}" \
							--label       "org.opencontainers.image.title=${env.APP_NAME}" \
							${pushArgs} --push .
					"""
				}
			}
		}
	}

	post {
		always {
			echo "🔒 Harbor 로그아웃"
			sh "docker logout ${DOCKER_REGISTRY} || true"
			cleanWs()
		}
		success {
			script {
				def latestLine = env.IS_MAIN == 'true' ? "  🏷️  LATEST : ${LATEST_TAG}\n" : ''
				echo """
╔══════════════════════════════════════════╗
  ✅  BUILD SUCCESS  #${env.BUILD_NUMBER}
╠══════════════════════════════════════════╣
  🐳  IMAGE  : ${IMAGE_TAG}
${latestLine}  📦  COMMIT : ${GIT_HASH} — ${env.GIT_MESSAGE}
  🌿  BRANCH : ${env.BRANCH_NAME}
╠══════════════════════════════════════════╣
  💡 Pull 명령어
  macOS         : docker pull --platform linux/amd64 ${IMAGE_TAG}
  Linux/Windows : docker pull ${IMAGE_TAG}
╚══════════════════════════════════════════╝
"""
			}
		}
		failure {
			echo """
╔══════════════════════════════════════════╗
  ❌  BUILD FAILED  #${env.BUILD_NUMBER}
╠══════════════════════════════════════════╣
  🌿  BRANCH : ${env.BRANCH_NAME}
  📦  COMMIT : ${GIT_HASH}
╚══════════════════════════════════════════╝
"""
		}
	}
}
