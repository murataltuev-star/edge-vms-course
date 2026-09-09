"""SigV4 against Amazon's published worked example — the GET Object case
from the S3 "Signature Calculations" documentation. If this signature
matches, the canonical request, the string to sign and the key derivation
are all right; nothing else in the adapter is cryptographic."""
import datetime as dt
from cluster.s3 import sign

ACCESS = "AKIAIOSFODNN7EXAMPLE"
SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


def test_amazon_worked_example_get_object():
    now = dt.datetime(2013, 5, 24, 0, 0, 0, tzinfo=dt.timezone.utc)
    h = sign("GET", "examplebucket.s3.amazonaws.com", "/test.txt", "",
             {"Range": "bytes=0-9"}, b"", ACCESS, SECRET, "us-east-1", now)
    assert h["x-amz-content-sha256"] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert h["authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")


def test_amazon_worked_example_put_object():
    """The PUT example from the same page: key test$file.text, body 'Welcome to Amazon S3.'"""
    now = dt.datetime(2013, 5, 24, 0, 0, 0, tzinfo=dt.timezone.utc)
    body = b"Welcome to Amazon S3."
    h = sign("PUT", "examplebucket.s3.amazonaws.com", "/test$file.text", "",
             {"Date": "Fri, 24 May 2013 00:00:00 GMT", "x-amz-storage-class": "REDUCED_REDUNDANCY"},
             body, ACCESS, SECRET, "us-east-1", now)
    assert h["x-amz-content-sha256"] == "44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072"
    assert h["authorization"].endswith("Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd")
