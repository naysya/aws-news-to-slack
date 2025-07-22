from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="aws-news-to-slack",
    version="1.0.0",
    author="Your Name",
    author_email="your.email@example.com",
    description="AWS 뉴스를 자동으로 수집하여 Slack으로 전송하는 Lambda 함수",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/aws-news-to-slack",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Topic :: Communications :: Chat",
        "Topic :: Internet :: WWW/HTTP :: Dynamic Content :: News/Diary",
    ],
    python_requires=">=3.9",
    install_requires=requirements,
    keywords="aws, lambda, slack, news, automation, bedrock, dynamodb",
    project_urls={
        "Bug Reports": "https://github.com/naysya/aws-news-to-slack/issues",
        "Source": "https://github.com/naysya/aws-news-to-slack",
    },
)