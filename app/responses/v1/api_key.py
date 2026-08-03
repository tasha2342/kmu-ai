from pydantic import BaseModel, Field


class ApiKeyResponse(BaseModel):
    """API Key 발급 응답 데이터"""
    
    api_key: str = Field(
		...,
		description="발급된 API Key입니다.",
		examples=["jd-TVlMaVY5aXduSkYyUENGQnJSYkhJSjVRZDExMDdiNGUtOTM2"]
	)
