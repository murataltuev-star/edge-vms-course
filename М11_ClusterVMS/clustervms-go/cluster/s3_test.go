package cluster

// SigV4 against Amazon's published worked examples — the GET Object and
// PUT Object cases from the S3 "Signature Calculations" documentation. If
// these signatures match, the canonical request, the string to sign and the
// key derivation are all right; nothing else in the adapter is cryptographic.

import (
	"strings"
	"testing"
	"time"
)

const (
	access = "AKIAIOSFODNN7EXAMPLE"
	secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
)

var may24 = time.Date(2013, 5, 24, 0, 0, 0, 0, time.UTC)

func TestAmazonWorkedExampleGetObject(t *testing.T) {
	h := Sign("GET", "examplebucket.s3.amazonaws.com", "/test.txt", "", map[string]string{"Range": "bytes=0-9"},
		nil, access, secret, "us-east-1", may24, "s3")
	if h["x-amz-content-sha256"] != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855" {
		t.Fatal(h["x-amz-content-sha256"])
	}
	want := "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, " +
		"SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, " +
		"Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
	if h["authorization"] != want {
		t.Fatalf("\n got %s\nwant %s", h["authorization"], want)
	}
}

func TestAmazonWorkedExamplePutObject(t *testing.T) {
	h := Sign("PUT", "examplebucket.s3.amazonaws.com", "/test$file.text", "",
		map[string]string{"Date": "Fri, 24 May 2013 00:00:00 GMT", "x-amz-storage-class": "REDUCED_REDUNDANCY"},
		[]byte("Welcome to Amazon S3."), access, secret, "us-east-1", may24, "s3")
	if h["x-amz-content-sha256"] != "44ce7dd67c959e0d3524ffac1771dfbba87d2b6b4b4e99e42034a8b803f8b072" {
		t.Fatal(h["x-amz-content-sha256"])
	}
	if !strings.HasSuffix(h["authorization"], "Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd") {
		t.Fatal(h["authorization"])
	}
}
