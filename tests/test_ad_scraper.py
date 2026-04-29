from backend.worker.ad_scraper import normalize_ad


def test_normalize_ad_extracts_snake_case_media_fields():
    raw = {
        "ad_archive_id": "914351827969140",
        "is_active": True,
        "start_date": 1711929600,
        "publisher_platforms": ["facebook"],
        "snapshot": {
            "body": {"text": "Try the new plan today"},
            "title": "Snake Case Headline",
            "cta_text": "Learn More",
            "link_url": "https://example.com/snake",
            "page_name": "Example Brand",
            "page_id": "12345",
            "videos": [
                {
                    "video_hd_url": "https://video.example/hd.mp4",
                    "video_sd_url": "https://video.example/sd.mp4",
                }
            ],
        },
    }

    ad = normalize_ad(raw)

    assert ad["meta_ad_id"] == "914351827969140"
    assert ad["status"] == "ACTIVE"
    assert ad["start_date"] == "2024-04-01"
    assert ad["headline"] == "Snake Case Headline"
    assert ad["cta"] == "Learn More"
    assert ad["video_url"] == "https://video.example/hd.mp4"
    assert ad["image_url"] is None
    assert ad["media_type"] == "video"
    assert ad["landing_page_url"] == "https://example.com/snake"
    assert ad["advertiser_name"] == "Example Brand"
    assert ad["page_id"] == "12345"
    assert ad["platforms"] == ["facebook"]


def test_normalize_ad_extracts_camel_case_media_fields():
    raw = {
        "adArchiveId": "23877400824600796",
        "isActive": True,
        "startDate": "2026-04-20T00:00:00+00:00",
        "publisherPlatforms": ["facebook", "instagram"],
        "euTotalReach": {"lower_bound": 1000, "upper_bound": 4999},
        "snapshot": {
            "body": {"text": "Camel case body"},
            "linkTitle": "Camel Case Headline",
            "ctaText": "Shop Now",
            "linkUrl": "https://example.com/camel",
            "pageName": "Camel Brand",
            "pageId": "67890",
            "images": [
                {
                    "originalImageUrl": "https://image.example/original.jpg",
                    "resizedImageUrl": "https://image.example/resized.jpg",
                }
            ],
        },
    }

    ad = normalize_ad(raw)

    assert ad["meta_ad_id"] == "23877400824600796"
    assert ad["status"] == "ACTIVE"
    assert ad["start_date"] == "2026-04-20"
    assert ad["body_text"] == "Camel case body"
    assert ad["headline"] == "Camel Case Headline"
    assert ad["cta"] == "Shop Now"
    assert ad["image_url"] == "https://image.example/original.jpg"
    assert ad["video_url"] is None
    assert ad["media_type"] == "image"
    assert ad["landing_page_url"] == "https://example.com/camel"
    assert ad["advertiser_name"] == "Camel Brand"
    assert ad["page_id"] == "67890"
    assert ad["platforms"] == ["facebook", "instagram"]
    assert ad["impression_range"] == {"lower_bound": 1000, "upper_bound": 4999}


def test_normalize_ad_extracts_camel_case_card_video():
    raw = {
        "adArchiveID": "card-video-1",
        "snapshot": {
            "cards": [
                {
                    "videoHdUrl": "https://video.example/card-hd.mp4",
                    "originalImageUrl": "https://image.example/card.jpg",
                }
            ]
        },
    }

    ad = normalize_ad(raw)

    assert ad["meta_ad_id"] == "card-video-1"
    assert ad["video_url"] == "https://video.example/card-hd.mp4"
    assert ad["image_url"] == "https://image.example/card.jpg"
    assert ad["media_type"] == "video"
