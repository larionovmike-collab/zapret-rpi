# Bounded strategy set for the web UI. Unlike upstream "standard", this set
# has a predictable 20 candidates per domain and does not expand TTL ranges.

pktws_check_http()
{
	local testf=$1 domain="$2" ok=0
	pktws_curl_test_update "$testf" "$domain" --payload=http_req --lua-desync=http_hostcase && ok=1
	pktws_curl_test_update "$testf" "$domain" --payload=http_req --lua-desync=multisplit:pos=method+2 && ok=1
	pktws_curl_test_update "$testf" "$domain" --payload=http_req --lua-desync=multidisorder:pos=method+2,midsld && ok=1
	pktws_curl_test_update "$testf" "$domain" --payload=http_req --lua-desync=fake:blob=fake_default_http:tcp_md5:repeats=1 && ok=1
	pktws_curl_test_update "$testf" "$domain" --payload=http_req --lua-desync=fake:blob=fake_default_http:badsum:repeats=1 && ok=1
	pktws_curl_test_update "$testf" "$domain" --payload=http_req --lua-desync=fake:blob=fake_default_http:tcp_md5:repeats=1 --lua-desync=multisplit:pos=method+2 && ok=1
	[ "$ok" = 1 ]
}

pktws_check_https_tls12()
{
	local testf=$1 domain="$2" ok=0
	pktws_curl_test_update "$testf" "$domain" --payload=tls_client_hello --lua-desync=multisplit:pos=1,midsld && ok=1
	pktws_curl_test_update "$testf" "$domain" --payload=tls_client_hello --lua-desync=multidisorder:pos=1,midsld && ok=1
	pktws_curl_test_update "$testf" "$domain" --payload=tls_client_hello --lua-desync=fake:blob=fake_default_tls:tcp_md5:tcp_seq=-10000:repeats=1 && ok=1
	pktws_curl_test_update "$testf" "$domain" --payload=tls_client_hello --lua-desync=fake:blob=fake_default_tls:badsum:repeats=1 && ok=1
	pktws_curl_test_update "$testf" "$domain" --payload=tls_client_hello --lua-desync=fake:blob=fake_default_tls:tls_mod=rnd,dupsid:repeats=1 && ok=1
	pktws_curl_test_update "$testf" "$domain" --payload=tls_client_hello --lua-desync=fake:blob=fake_default_tls:tls_mod=rnd,dupsid,sni=google.com:repeats=1 && ok=1
	pktws_curl_test_update "$testf" "$domain" --payload=tls_client_hello --lua-desync=fake:blob=fake_default_tls:tcp_md5:repeats=1 --lua-desync=multisplit:pos=1,midsld && ok=1
	pktws_curl_test_update "$testf" "$domain" --payload=tls_client_hello --lua-desync=fake:blob=fake_default_tls:tcp_md5:repeats=1 --lua-desync=multidisorder:pos=1,midsld && ok=1
	pktws_curl_test_update "$testf" "$domain" --lua-desync=wssize:wsize=1:scale=6 --payload=tls_client_hello --lua-desync=multisplit:pos=1,midsld && ok=1
	[ "$ok" = 1 ]
}

pktws_check_https_tls13()
{
	pktws_check_https_tls12 "$@"
}

pktws_check_http3()
{
	local testf=$1 domain="$2" ok=0
	pktws_curl_test_update "$testf" "$domain" --payload=quic_initial --lua-desync=fake:blob=fake_default_quic:repeats=2 && ok=1
	pktws_curl_test_update "$testf" "$domain" --payload=quic_initial --lua-desync=fake:blob=fake_default_quic:repeats=6 && ok=1
	pktws_curl_test_update "$testf" "$domain" --payload=quic_initial --lua-desync=fake:blob=fake_default_quic:repeats=10 && ok=1
	pktws_curl_test_update "$testf" "$domain" --payload=quic_initial --lua-desync=send:ipfrag:ipfrag_pos_udp=32 --lua-desync=drop && ok=1
	pktws_curl_test_update "$testf" "$domain" --payload=quic_initial --lua-desync=fake:blob=fake_default_quic:repeats=6 --lua-desync=send:ipfrag:ipfrag_pos_udp=32 --lua-desync=drop && ok=1
	[ "$ok" = 1 ]
}
