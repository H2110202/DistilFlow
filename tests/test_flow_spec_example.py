"""
DistilFlow - FlowSpec end-to-end test example.
Replace the URL and template_id with your own OA system before running.
"""
import asyncio
import json
import httpx


BASE_URL = "http://your-oa-server.example.com"
TEMPLATE_ID = "your-template-id-here"


async def test_submit():
    async with httpx.AsyncClient(base_url=BASE_URL, follow_redirects=True, timeout=30) as client:
        form_data = {
            "extendDataFormInfo.value(fd_your_field_id)": "<p>Test content</p>",
            "docSubject": "Test Submission",
        }
        resp = await client.post(
            "/km/review/km_review_main/kmReviewMain.do",
            data={"method": "add", "fdTemplateId": TEMPLATE_ID},
        )
        print(f"Create status: {resp.status_code}")


if __name__ == "__main__":
    asyncio.run(test_submit())
