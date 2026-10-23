--
-- PostgreSQL database dump
--

\restrict fDc5s6M2bzZwKVNCFrUdSf2CmnnanTj2G9FlncGudFGLWMigimAKV1BUjdadtgC

-- Dumped from database version 18.6
-- Dumped by pg_dump version 18.6

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: app_metadata; Type: TABLE; Schema: public; Owner: storeshare_app
--

CREATE TABLE public.app_metadata (
    key character varying(128) NOT NULL,
    value text NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


ALTER TABLE public.app_metadata OWNER TO storeshare_app;

--
-- Name: chunk_jobs; Type: TABLE; Schema: public; Owner: storeshare_app
--

CREATE TABLE public.chunk_jobs (
    id bigint NOT NULL,
    provider_node_id bigint NOT NULL,
    chunk_id character varying(128) NOT NULL,
    file_id bigint NOT NULL,
    job_type character varying(32) NOT NULL,
    status character varying(32) DEFAULT 'QUEUED'::character varying NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    error_message text,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chunk_jobs_job_type_check CHECK (((job_type)::text = ANY ((ARRAY['STORE_CHUNK'::character varying, 'RETRIEVE_CHUNK'::character varying, 'DELETE_CHUNK'::character varying])::text[]))),
    CONSTRAINT chunk_jobs_status_check CHECK (((status)::text = ANY ((ARRAY['QUEUED'::character varying, 'PROCESSING'::character varying, 'COMPLETED'::character varying, 'FAILED'::character varying, 'TIMED_OUT'::character varying])::text[])))
);


ALTER TABLE public.chunk_jobs OWNER TO storeshare_app;

--
-- Name: chunk_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: storeshare_app
--

CREATE SEQUENCE public.chunk_jobs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.chunk_jobs_id_seq OWNER TO storeshare_app;

--
-- Name: chunk_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: storeshare_app
--

ALTER SEQUENCE public.chunk_jobs_id_seq OWNED BY public.chunk_jobs.id;


--
-- Name: file_chunks; Type: TABLE; Schema: public; Owner: storeshare_app
--

CREATE TABLE public.file_chunks (
    id bigint NOT NULL,
    file_id bigint NOT NULL,
    chunk_id character varying(128) NOT NULL,
    provider_node_id bigint NOT NULL,
    chunk_index integer DEFAULT 0 NOT NULL,
    chunk_size bigint NOT NULL,
    status character varying(32) DEFAULT 'PENDING'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    last_verified_at timestamp with time zone,
    sha256_hash character varying(64),
    CONSTRAINT file_chunks_status_check CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying, 'STORED'::character varying, 'FAILED'::character varying])::text[])))
);


ALTER TABLE public.file_chunks OWNER TO storeshare_app;

--
-- Name: file_chunks_id_seq; Type: SEQUENCE; Schema: public; Owner: storeshare_app
--

CREATE SEQUENCE public.file_chunks_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.file_chunks_id_seq OWNER TO storeshare_app;

--
-- Name: file_chunks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: storeshare_app
--

ALTER SEQUENCE public.file_chunks_id_seq OWNED BY public.file_chunks.id;


--
-- Name: files; Type: TABLE; Schema: public; Owner: storeshare_app
--

CREATE TABLE public.files (
    id bigint NOT NULL,
    owner_id bigint NOT NULL,
    filename character varying(512) NOT NULL,
    file_size bigint NOT NULL,
    encrypted_size bigint NOT NULL,
    iv_hex character varying(64) NOT NULL,
    sha256_hash character varying(64) NOT NULL,
    status character varying(32) DEFAULT 'PENDING_UPLOAD'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT files_status_check CHECK (((status)::text = ANY ((ARRAY['PENDING_UPLOAD'::character varying, 'UPLOADING'::character varying, 'STORED'::character varying, 'FAILED'::character varying, 'DELETED'::character varying])::text[])))
);


ALTER TABLE public.files OWNER TO storeshare_app;

--
-- Name: files_id_seq; Type: SEQUENCE; Schema: public; Owner: storeshare_app
--

CREATE SEQUENCE public.files_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.files_id_seq OWNER TO storeshare_app;

--
-- Name: files_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: storeshare_app
--

ALTER SEQUENCE public.files_id_seq OWNED BY public.files.id;


--
-- Name: provider_nodes; Type: TABLE; Schema: public; Owner: storeshare_app
--

CREATE TABLE public.provider_nodes (
    id bigint NOT NULL,
    provider_id bigint NOT NULL,
    storage_dir text,
    quota_bytes bigint DEFAULT 0 NOT NULL,
    used_bytes bigint DEFAULT 0 NOT NULL,
    reserved_bytes bigint DEFAULT 0 NOT NULL,
    status character varying(32) DEFAULT 'UNREGISTERED'::character varying NOT NULL,
    last_heartbeat timestamp with time zone,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    computer_name character varying(255),
    free_disk_bytes bigint DEFAULT 0 NOT NULL,
    last_inventory text,
    CONSTRAINT provider_nodes_status_check CHECK (((status)::text = ANY ((ARRAY['UNREGISTERED'::character varying, 'ONLINE'::character varying, 'OFFLINE'::character varying, 'STOPPED'::character varying, 'PAUSED'::character varying])::text[])))
);


ALTER TABLE public.provider_nodes OWNER TO storeshare_app;

--
-- Name: provider_nodes_id_seq; Type: SEQUENCE; Schema: public; Owner: storeshare_app
--

CREATE SEQUENCE public.provider_nodes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.provider_nodes_id_seq OWNER TO storeshare_app;

--
-- Name: provider_nodes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: storeshare_app
--

ALTER SEQUENCE public.provider_nodes_id_seq OWNED BY public.provider_nodes.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: storeshare_app
--

CREATE TABLE public.users (
    id bigint NOT NULL,
    email character varying(255) NOT NULL,
    password_hash character varying(255) NOT NULL,
    role character varying(32) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT users_role_check CHECK (((role)::text = ANY ((ARRAY['CONSUMER'::character varying, 'PROVIDER'::character varying, 'ADMIN'::character varying])::text[])))
);


ALTER TABLE public.users OWNER TO storeshare_app;

--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: storeshare_app
--

CREATE SEQUENCE public.users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.users_id_seq OWNER TO storeshare_app;

--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: storeshare_app
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: chunk_jobs id; Type: DEFAULT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.chunk_jobs ALTER COLUMN id SET DEFAULT nextval('public.chunk_jobs_id_seq'::regclass);


--
-- Name: file_chunks id; Type: DEFAULT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.file_chunks ALTER COLUMN id SET DEFAULT nextval('public.file_chunks_id_seq'::regclass);


--
-- Name: files id; Type: DEFAULT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.files ALTER COLUMN id SET DEFAULT nextval('public.files_id_seq'::regclass);


--
-- Name: provider_nodes id; Type: DEFAULT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.provider_nodes ALTER COLUMN id SET DEFAULT nextval('public.provider_nodes_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Data for Name: app_metadata; Type: TABLE DATA; Schema: public; Owner: storeshare_app
--

COPY public.app_metadata (key, value, created_at) FROM stdin;
database_driver	SQLite	2026-10-15 18:11:09+05:30
phase	Day 1 Foundation	2026-10-15 18:11:09+05:30
project_name	Store-Share	2026-10-15 18:11:09+05:30
\.


--
-- Data for Name: chunk_jobs; Type: TABLE DATA; Schema: public; Owner: storeshare_app
--

COPY public.chunk_jobs (id, provider_node_id, chunk_id, file_id, job_type, status, expires_at, error_message, created_at, updated_at) FROM stdin;
1	4	chunk_live_470af6250ff8	1	STORE_CHUNK	COMPLETED	2026-10-17 02:41:04+05:30	\N	2026-10-17 02:40:04+05:30	2026-10-17 02:40:04+05:30
2	4	chunk_live_470af6250ff8	1	RETRIEVE_CHUNK	COMPLETED	2026-10-17 02:41:04+05:30	\N	2026-10-17 02:40:04+05:30	2026-10-17 02:40:04+05:30
3	4	chunk_live_470af6250ff8	1	DELETE_CHUNK	COMPLETED	2026-10-17 02:41:04+05:30	\N	2026-10-17 02:40:04+05:30	2026-10-17 02:40:04+05:30
4	5	chunk_live_681f7cdb0626	2	STORE_CHUNK	COMPLETED	2026-10-17 02:47:25+05:30	\N	2026-10-17 02:46:25+05:30	2026-10-17 02:46:25+05:30
5	5	chunk_live_681f7cdb0626	2	RETRIEVE_CHUNK	COMPLETED	2026-10-17 02:47:25+05:30	\N	2026-10-17 02:46:25+05:30	2026-10-17 02:46:25+05:30
6	5	chunk_live_681f7cdb0626	2	DELETE_CHUNK	COMPLETED	2026-10-17 02:47:25+05:30	\N	2026-10-17 02:46:25+05:30	2026-10-17 02:46:25+05:30
7	7	chunk_984cd8077c0849f08edfa4cc38f5200a	3	STORE_CHUNK	COMPLETED	2026-10-17 03:03:56+05:30	\N	2026-10-17 03:02:56+05:30	2026-10-17 03:02:56+05:30
8	7	chunk_984cd8077c0849f08edfa4cc38f5200a	3	RETRIEVE_CHUNK	COMPLETED	2026-10-17 03:03:26+05:30	\N	2026-10-17 03:02:56+05:30	2026-10-17 03:02:57+05:30
9	7	chunk_984cd8077c0849f08edfa4cc38f5200a	3	DELETE_CHUNK	COMPLETED	2026-10-17 03:03:17+05:30	\N	2026-10-17 03:02:57+05:30	2026-10-17 03:02:57+05:30
10	8	chunk_251c330c1f5a41028a67ee4887f7c235	4	STORE_CHUNK	COMPLETED	2026-10-17 13:05:50+05:30	\N	2026-10-17 13:04:50+05:30	2026-10-17 13:04:51+05:30
11	8	chunk_251c330c1f5a41028a67ee4887f7c235	4	RETRIEVE_CHUNK	COMPLETED	2026-10-17 13:05:21+05:30	\N	2026-10-17 13:04:51+05:30	2026-10-17 13:04:51+05:30
12	8	chunk_251c330c1f5a41028a67ee4887f7c235	4	DELETE_CHUNK	COMPLETED	2026-10-17 13:05:11+05:30	\N	2026-10-17 13:04:51+05:30	2026-10-17 13:04:52+05:30
13	9	chunk_b3c22154c0424ea6ae727a83a31a3fc1	5	STORE_CHUNK	COMPLETED	2026-10-17 13:09:20+05:30	\N	2026-10-17 13:08:20+05:30	2026-10-17 13:08:20+05:30
14	9	chunk_b3c22154c0424ea6ae727a83a31a3fc1	5	RETRIEVE_CHUNK	COMPLETED	2026-10-17 13:08:51+05:30	\N	2026-10-17 13:08:21+05:30	2026-10-17 13:08:22+05:30
15	9	chunk_b3c22154c0424ea6ae727a83a31a3fc1	5	DELETE_CHUNK	COMPLETED	2026-10-17 13:08:42+05:30	\N	2026-10-17 13:08:22+05:30	2026-10-17 13:08:22+05:30
80	10	chunk_d23290e92b64465cae482792afe75f2b	36	DELETE_CHUNK	COMPLETED	2026-10-20 01:25:47.486763+05:30	\N	2026-10-20 01:25:27.644123+05:30	2026-10-20 01:25:30.586137+05:30
137	2	chunk_fc1ac0738c35467199fe8cc185d87cff	63	STORE_CHUNK	TIMED_OUT	2026-10-23 20:56:43.447064+05:30	Job timed out before agent acknowledgement.	2026-10-23 20:55:43.677934+05:30	2026-10-23 20:57:48.639623+05:30
81	10	chunk_c093aa2ab6e94bfa9a4f87aeec32ba50	37	STORE_CHUNK	COMPLETED	2026-10-20 22:51:11.592955+05:30	\N	2026-10-20 22:50:11.748073+05:30	2026-10-20 22:50:14.539943+05:30
138	2	chunk_50f7d36cde32473bb672c23afe4c5dd9	64	STORE_CHUNK	COMPLETED	2026-10-23 20:58:47.736058+05:30	\N	2026-10-23 20:57:47.856431+05:30	2026-10-23 20:57:50.401436+05:30
69	10	chunk_dbe84391fc2f4ecd8df6909f3068c84c	32	STORE_CHUNK	COMPLETED	2026-10-19 20:41:51.489165+05:30	\N	2026-10-19 20:40:51.609468+05:30	2026-10-19 20:40:53.944445+05:30
70	10	chunk_dbe84391fc2f4ecd8df6909f3068c84c	32	RETRIEVE_CHUNK	COMPLETED	2026-10-19 20:41:59.319759+05:30	\N	2026-10-19 20:41:29.571326+05:30	2026-10-19 20:41:32.080249+05:30
96	10	chunk_c093aa2ab6e94bfa9a4f87aeec32ba50	37	DELETE_CHUNK	COMPLETED	2026-10-22 13:07:56.3858+05:30	\N	2026-10-22 13:07:36.488315+05:30	2026-10-22 13:07:38.315595+05:30
97	10	chunk_6ef1c34b75d048629d6890da5f95e5f6	44	STORE_CHUNK	COMPLETED	2026-10-22 13:12:53.304119+05:30	\N	2026-10-22 13:11:53.42034+05:30	2026-10-22 13:11:56.00768+05:30
98	10	chunk_1e3db41a2ffa46da9d53ab1a4712315e	45	STORE_CHUNK	COMPLETED	2026-10-22 16:10:40.067777+05:30	\N	2026-10-22 16:09:40.186373+05:30	2026-10-22 16:09:44.511705+05:30
99	10	chunk_52dbc0f02cba446291833832ff7bd265	46	STORE_CHUNK	COMPLETED	2026-10-22 16:51:33.11945+05:30	\N	2026-10-22 16:50:33.236787+05:30	2026-10-22 16:50:35.90428+05:30
78	10	chunk_dbe84391fc2f4ecd8df6909f3068c84c	32	DELETE_CHUNK	COMPLETED	2026-10-20 01:16:20.237319+05:30	\N	2026-10-20 01:16:00.34998+05:30	2026-10-20 01:16:02.91762+05:30
128	10	chunk_7fb4ecf730f14180a48e725a4c9ee778	59	STORE_CHUNK	COMPLETED	2026-10-23 15:37:27.122353+05:30	\N	2026-10-23 15:36:27.251646+05:30	2026-10-23 15:36:31.957951+05:30
79	10	chunk_d23290e92b64465cae482792afe75f2b	36	STORE_CHUNK	COMPLETED	2026-10-20 01:19:19.016988+05:30	\N	2026-10-20 01:18:19.172139+05:30	2026-10-20 01:18:22.163341+05:30
129	10	chunk_7fb4ecf730f14180a48e725a4c9ee778	59	RETRIEVE_CHUNK	COMPLETED	2026-10-23 15:37:05.223494+05:30	\N	2026-10-23 15:36:35.350486+05:30	2026-10-23 15:36:37.518259+05:30
\.


--
-- Data for Name: file_chunks; Type: TABLE DATA; Schema: public; Owner: storeshare_app
--

COPY public.file_chunks (id, file_id, chunk_id, provider_node_id, chunk_index, chunk_size, status, created_at, last_verified_at, sha256_hash) FROM stdin;
38	63	chunk_fc1ac0738c35467199fe8cc185d87cff	2	0	78	FAILED	2026-10-23 20:55:43.284633+05:30	\N	\N
39	64	chunk_50f7d36cde32473bb672c23afe4c5dd9	2	0	79	STORED	2026-10-23 20:57:47.576212+05:30	2026-10-23 20:57:51.883817+05:30	\N
25	45	chunk_1e3db41a2ffa46da9d53ab1a4712315e	10	0	80313	STORED	2026-10-22 16:09:39.911055+05:30	2026-10-23 23:37:49.013311+05:30	\N
24	44	chunk_6ef1c34b75d048629d6890da5f95e5f6	10	0	700259	STORED	2026-10-22 13:11:53.119853+05:30	2026-10-23 23:37:49.013311+05:30	\N
26	46	chunk_52dbc0f02cba446291833832ff7bd265	10	0	803558	STORED	2026-10-22 16:50:32.915655+05:30	2026-10-23 23:37:49.013311+05:30	\N
35	59	chunk_7fb4ecf730f14180a48e725a4c9ee778	10	0	95	STORED	2026-10-23 15:36:26.932801+05:30	2026-10-23 23:37:49.013311+05:30	\N
\.


--
-- Data for Name: files; Type: TABLE DATA; Schema: public; Owner: storeshare_app
--

COPY public.files (id, owner_id, filename, file_size, encrypted_size, iv_hex, sha256_hash, status, created_at, updated_at) FROM stdin;
1	15	live_confidential_document.pdf	52	68	be40af4d2c815e4a2e659bd2	0e0c56c0c609810d68d4d872737724909fbaa04c188af3654bb33bcac1fd5c03	DELETED	2026-10-17 02:40:04+05:30	2026-10-17 02:40:04+05:30
2	17	live_confidential_document.pdf	52	68	1b1a896f8dc90d80e355b908	bd3c43bd8ae22a0a5da1fb0645bd5489d384044b53b7008dcea5c49742a3582a	DELETED	2026-10-17 02:46:25+05:30	2026-10-17 02:46:25+05:30
3	20	quarterly_financials_2026.pdf	128	144	91554e461d8dc6bbbb10ef30	7f92e5033347e2ba05320f5d3b6ed44158a57dc146c89432646d625104b09466	DELETED	2026-10-17 03:02:56+05:30	2026-10-17 03:02:57+05:30
4	37	phase4_verified_doc.txt	110	126	a6329cb1eed440b1d7b8fc07	ddac1180043319c7db2b2f764e8d14d33c2452f5622fe1d57f7e41a7463db269	DELETED	2026-10-17 13:04:50+05:30	2026-10-17 13:04:52+05:30
5	39	phase4_verified_doc.txt	110	126	4b68d60aa52fec9449ae11ff	c796b062ae6885eaea0a8b4629a4c01a70a03cd1029e114f7f261d2b08af86f4	DELETED	2026-10-17 13:08:20+05:30	2026-10-17 13:08:22+05:30
32	5	Manikandand_resume (2).pdf	177162	177178	bc8b3ac866f560c56cd17276	202ef479c987141c3bc7c4fac5724f0108190e4f447b69117c4d28efa3a29279	DELETED	2026-10-19 20:40:50.882568+05:30	2026-10-20 01:16:03.431289+05:30
36	5	Manikandand_resume (2) (1).pdf	177162	177178	b25224a61ffd3fa9efadf9cd	b46495cd729f8750b00f0f0414f6b32d16866afb5d3df883c3890076e6dbd896	DELETED	2026-10-20 01:18:18.369759+05:30	2026-10-20 01:25:31.020736+05:30
59	215	e2e_verification_test.txt	79	95	aa30d0e713c9922024110629	a9ccda671a03b1039dfdedeb0fcd2c1f545f4ebaec42d81dfe29307604efa4ce	STORED	2026-10-23 15:36:26.656551+05:30	2026-10-23 15:36:31.957951+05:30
63	230	test_24d126_upload_1792769142.dat	62	78	1234567890abcdef12345678	938372083e9af7d35885085d39b0c05df27657e9ee1f2fab62cf5447bd7d89fb	FAILED	2026-10-23 20:55:43.008045+05:30	2026-10-23 20:57:48.639623+05:30
64	231	test_24d126_upload_1792769266.dat	63	79	1234567890abcdef12345678	8c6f5441c078273dbd93ce1b8aa7d65ab9fd54252e417d117069ba63d677e892	STORED	2026-10-23 20:57:47.317944+05:30	2026-10-23 20:57:50.401436+05:30
37	5	Manikandand_resume (2) (1).pdf	177162	177178	47c93013fdbfc0280201f5f9	52d7e56c08214c368b18ceeaebd368ef489e830c9dee5b71a3d0965ec6762eed	DELETED	2026-10-20 22:50:11.116648+05:30	2026-10-22 13:07:38.822241+05:30
44	5	NOC26CS171S774100050.pdf	700243	700259	591276be7a669baadbff5635	406d6025b4548aac1d15813b2affea0a90a4021fdc72f987ba9fd87ef39e6157	STORED	2026-10-22 13:11:52.844574+05:30	2026-10-22 13:11:56.00768+05:30
45	20	confidential_audit_report_2026.pdf	80297	80313	5f97865bd3ccd9d48ea57388	6a1d1aaa4b64dd48c4c9a0db32a4bce9ea331206bd8138e92fc98c044b4047ca	STORED	2026-10-22 16:09:39.659485+05:30	2026-10-22 16:09:44.511705+05:30
46	5	CC Assignment4 (1).pdf	803542	803558	bb24bdac8af30435abb88a1a	32aa1478c2a5c4d184c270822320c916fab0957d782629fc8e13e94e6ffc0715	STORED	2026-10-22 16:50:32.603296+05:30	2026-10-22 16:50:35.90428+05:30
\.


--
-- Data for Name: provider_nodes; Type: TABLE DATA; Schema: public; Owner: storeshare_app
--

COPY public.provider_nodes (id, provider_id, storage_dir, quota_bytes, used_bytes, reserved_bytes, status, last_heartbeat, created_at, updated_at, computer_name, free_disk_bytes, last_inventory) FROM stdin;
1	3	C:\\Projects\\storeshare\\data\\provider1_storage	5368709120	0	0	ONLINE	2026-10-16 19:48:24+05:30	2026-10-16 19:48:24+05:30	2026-10-16 19:48:24+05:30	\N	0	\N
4	14	C:\\Users\\Barnika\\AppData\\Local\\Temp\\storeshare_live_provider_c4kbjzak	53687091	0	0	ONLINE	2026-10-17 02:40:04+05:30	2026-10-17 02:40:04+05:30	2026-10-17 02:40:04+05:30	\N	0	\N
5	16	C:\\Users\\Barnika\\AppData\\Local\\Temp\\storeshare_live_provider_4d79vvmq	53687091	0	0	ONLINE	2026-10-17 02:46:25+05:30	2026-10-17 02:46:25+05:30	2026-10-17 02:46:25+05:30	\N	0	\N
6	19	C:/temp_prov	10000000	0	0	ONLINE	2026-10-17 02:58:10+05:30	2026-10-17 02:58:10+05:30	2026-10-17 02:58:10+05:30	\N	0	\N
7	21	C:\\Users\\Barnika\\AppData\\Local\\Temp\\storeshare_p3_provider_bmfvliu4	104857600	0	0	ONLINE	2026-10-17 03:02:55+05:30	2026-10-17 03:02:55+05:30	2026-10-17 03:02:57+05:30	\N	0	\N
8	38	C:\\Users\\Barnika\\AppData\\Local\\Temp\\storeshare_p4_prov_96g4g3gi	104857600	0	0	ONLINE	2026-10-17 13:04:49+05:30	2026-10-17 13:04:49+05:30	2026-10-17 13:04:52+05:30	\N	0	\N
9	40	C:\\Users\\Barnika\\AppData\\Local\\Temp\\storeshare_p4_prov_n3h47nb2	104857600	0	0	ONLINE	2026-10-17 13:08:19+05:30	2026-10-17 13:08:18+05:30	2026-10-17 13:08:22+05:30	\N	0	\N
10	42	C:\\Projects\\storeshare\\data\\provider_storage	10737418240	1584225	0	ONLINE	2026-10-23 23:37:48.762051+05:30	2026-10-17 13:24:40+05:30	2026-10-23 23:37:48.762051+05:30	OMEN	767141023744	[{"chunk_id": "chunk_1e3db41a2ffa46da9d53ab1a4712315e", "size_bytes": 80313, "sha256_hash": "6a1d1aaa4b64dd48c4c9a0db32a4bce9ea331206bd8138e92fc98c044b4047ca", "modified_at": 1792665583}, {"chunk_id": "chunk_52dbc0f02cba446291833832ff7bd265", "size_bytes": 803558, "sha256_hash": "32aa1478c2a5c4d184c270822320c916fab0957d782629fc8e13e94e6ffc0715", "modified_at": 1792668034}, {"chunk_id": "chunk_6ef1c34b75d048629d6890da5f95e5f6", "size_bytes": 700259, "sha256_hash": "406d6025b4548aac1d15813b2affea0a90a4021fdc72f987ba9fd87ef39e6157", "modified_at": 1792654915}, {"chunk_id": "chunk_7fb4ecf730f14180a48e725a4c9ee778", "size_bytes": 95, "sha256_hash": "a9ccda671a03b1039dfdedeb0fcd2c1f545f4ebaec42d81dfe29307604efa4ce", "modified_at": 1792749990}]
2	6	C:\\storeshare-storage	53687091200	79	0	ONLINE	2026-10-23 20:57:50.954103+05:30	2026-10-16 20:20:48+05:30	2026-10-23 20:57:50.954103+05:30	OMEN	767724949504	[{"chunk_id": "chunk_50f7d36cde32473bb672c23afe4c5dd9", "size_bytes": 79, "sha256_hash": "8c6f5441c078273dbd93ce1b8aa7d65ab9fd54252e417d117069ba63d677e892", "modified_at": 1792769269}]
\.


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: storeshare_app
--

COPY public.users (id, email, password_hash, role, is_active, created_at) FROM stdin;
1	liveconsumer@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$Dp6ZH+meqmSkLcIrSDGGQw$VdEtDS/fFsIilgON+4nvxZBACGj8Lpkqz+Nk9BKV5RE	CONSUMER	t	2026-10-15 18:39:11+05:30
2	admin@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$t+F4SBSWcQB7KenTq9gdzA$DhBosbpFVasL2Lm5iuLgK8B6YXarr+diY3muYFkNAwo	ADMIN	t	2026-10-15 18:41:20+05:30
3	provider1@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$jzWvWbHPhnmRV6N7EIk7SQ$7WALWkrjad3XLa1u1GyfWsKhO9fLej201KC7Nx1PQRs	PROVIDER	t	2026-10-15 18:41:53+05:30
4	fe_test_1792080593465@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$g0O2XhkcGLYgGjYIC77XFw$uX5oe3y6/Sv3RnOuNTvHO2vYcvB3aW36qTxHC1zPtAs	CONSUMER	t	2026-10-15 21:39:53+05:30
5	manikandandharmalingam9594@gmail.com	$argon2id$v=19$m=65536,t=3,p=4$GHdexmqEU6IrKgADmPktRw$v3fUUXw6xTG3chg8dTUukqR9t30QUV2teNS/shT+hF4	CONSUMER	t	2026-10-15 21:56:25+05:30
6	24d126@psgitech.ac.in	$argon2id$v=19$m=65536,t=3,p=4$JH1Ep0DEbzZIPufIcSl0FQ$TOAwfk6x+oFeQ996rD3egj/0TaTj5X/g9/Mf2ezDFq4	PROVIDER	t	2026-10-15 22:00:59+05:30
7	fe_test_1792159063629@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$IrFnDjQtcIW6e5etSbOkGA$bPu9QUJJl6FIOdnfY+E2EtzpMInIMmg3v1y6OCVk54c	CONSUMER	t	2026-10-16 19:27:43+05:30
8	fe_test_1792159340278@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$yn62FMUCl5SPTREtvl0a/A$DqAC1J3YO5fILHCoTgap2nKbJK07vs44fqiRz9OxPFg	CONSUMER	t	2026-10-16 19:32:20+05:30
9	fe_test_1792159972626@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$hW1bc8ZISzw9j7oN54pSHw$J2ifh2Ofh+rL7YqZXTN/9Y/k86KhCd5TlMKWjJVDUKU	CONSUMER	t	2026-10-16 19:42:52+05:30
10	fe_test_1792160128385@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$dayh8M4h5mxKJjsRHR06Rg$zuXZqxGu2Lj4t6l2EcWtsTvoknzpDSFsqH5ajc5bu8o	CONSUMER	t	2026-10-16 19:45:28+05:30
11	fe_test_1792160354754@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$kuZwSa7Vcq2QUD/Nt6Btbg$ppn6fP1WHg1YxWo9J6ZJMnYNh0CDijX6IZdm/jEEDuk	CONSUMER	t	2026-10-16 19:49:14+05:30
12	fe_test_1792160440552@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$eGbQmNfTWwVINsOE6dE0TA$ulMdzACED0T5/u+KT4vq64UToH3KMDjGHE/q5Mwdb0Y	CONSUMER	t	2026-10-16 19:50:40+05:30
13	fe_test_1792163571177@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$fVc7n6qFgTvdOiZyPTR+lQ$27uy0EmXmJMWHPiHGBPgMxHrlhOYrkflwn+3M6mJqPg	CONSUMER	t	2026-10-16 20:42:51+05:30
14	live_provider_e423a9@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$+BGQ3eZqWEWc/ctq4L6EEA$qzLoJFOTtKRe+WLjxxCOODBBDpT6Ka7oMjeX1BaihzQ	PROVIDER	t	2026-10-17 02:40:04+05:30
15	consumer_live_12dffe@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$9PDkigah1vnZsonXdH7xaQ$HNotp9QUhduU583dUGG0YEcvZ/pi1nK3gfoH7epzvo8	CONSUMER	t	2026-10-17 02:40:04+05:30
16	live_provider_934b7e@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$IRtbDyn1sy5N+A++ZTVbHg$QctqpMS8AZp+eZ3aN5ehYN2D5lRq5IcfEnGgUIpPJIY	PROVIDER	t	2026-10-17 02:46:25+05:30
17	consumer_live_62b6dc@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$hypThgrRrkuEy/lwdMBZqg$8mlM70D0jsWrYuODWsuBVfg2cItMtAxiLggW/hbSs9w	CONSUMER	t	2026-10-17 02:46:25+05:30
18	u1@ex.com	$argon2id$v=19$m=65536,t=3,p=4$CNXX4iRdIg4FuGv4ppy2Mw$GlvE24Qo9O12Ty987OLbJHwl2/PKZ+PktncEiMyQkAs	CONSUMER	t	2026-10-17 02:58:10+05:30
19	p1@ex.com	$argon2id$v=19$m=65536,t=3,p=4$YLo7CGWAiVyV1oDK4nGeoQ$hV2oas9PqOLxyJuEc2ehbpZl29yT6tsXkX4Um+JAscw	PROVIDER	t	2026-10-17 02:58:10+05:30
20	live_consumer_b54c8b@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$ot4fDf9PxAjy9UeKKBWj/w$AJ8QkTYMcLr8Me/FZ0PIpuJ8EI9CJWG/TFoisyMAMq4	CONSUMER	t	2026-10-17 03:02:55+05:30
21	live_prov_p3_5fc718@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$A9SdAEBzqJ91W/Y9xLnRCQ$kDeQoLGXrRCUOp8ThHWmwxGhd1KUpO+d7Jz6T8OdR40	PROVIDER	t	2026-10-17 03:02:55+05:30
22	fe_test_1792186613352@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$633WwEOPBxEtkVuiYtG8Sw$8Rj2aNrfN38uBKEpfM9rjCzuRgYPE4D4N2+p3St/Glc	CONSUMER	t	2026-10-17 03:06:53+05:30
23	fe_test_1792186710947@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$PhlUIDMeOE0OTEt7t3bnCA$2+H3fIfNBFL9+WNPRqVQ0w4hZMwVnWg/kUavgYnJpvs	CONSUMER	t	2026-10-17 03:08:31+05:30
24	fe_test_1792186906420@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$s8ZlcOYoc3TftQRU51qWcQ$6jiftPK/05JgZGzFU9r2bjsLaGl20fiHbxPfZ+LCcSM	CONSUMER	t	2026-10-17 03:11:46+05:30
25	fe_test_1792187014984@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$dc3DRzxu/g6A1wBR9I+yOQ$flPQvVgcp/cVJpq7MJ6ECwJ3t1zAwenR8hXrtY2n9Z0	CONSUMER	t	2026-10-17 03:13:35+05:30
26	consumer_limit_1792187016213@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$fQ62Py4oAPwYhZKpPFksng$fvm8oXY+KJSb1tWpsMg0vQ3derY/Yx5eY2tNO8LYBCg	CONSUMER	t	2026-10-17 03:13:36+05:30
27	consumer_hash_1792187016412@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$+aS8JeTl84vr3ppjNrMCYw$aSjcp1TEEGr3LqjwAu4jfgdQg9Yi0m6t8c6kpbc7CEk	CONSUMER	t	2026-10-17 03:13:36+05:30
28	consumer_list_1792187016594@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$+8A1ZOl1BZoWfVdAnO4VLA$N+UYRIpeCLVqODDSVavoGdiA52sqKevsNr6ksZEzVpY	CONSUMER	t	2026-10-17 03:13:36+05:30
29	fe_test_1792187047220@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$CFPkGDnZ4xEupypuFG1J4A$Hu1Bsofim0q94K2hB2OWaPkpXo638HjVPlY07UfuKDg	CONSUMER	t	2026-10-17 03:14:07+05:30
30	consumer_limit_1792187048462@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$fij6LSgI38SXw/+YAUel3g$BbLO1wUU3uTU0ecwRZA7cRzguQOj4TOss8v5bxGQwzY	CONSUMER	t	2026-10-17 03:14:08+05:30
31	consumer_hash_1792187048657@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$NN8UqhYkt0FnmFZrpbIZDw$lSiXdovMSi4iyaODIFlhGE0+W2fEaEC7MBBJl/Ygj28	CONSUMER	t	2026-10-17 03:14:08+05:30
32	consumer_list_1792187048831@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$wDpZH0IN8UksHi2Ap63Ceg$b1OQst+wN24A5eWGIt017cn/Ka4Zc9zfjiTQUxB6+tw	CONSUMER	t	2026-10-17 03:14:08+05:30
33	fe_test_1792221876203@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$3WdXM2wHnmPxCZzVSrcX0Q$8flQK/4mKsfAIlJFj8fOGHvubH8096BmCtkc/MnKJR4	CONSUMER	t	2026-10-17 12:54:36+05:30
34	fe_test_1792221995747@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$zNwTKrloy8KKBH/ZPIWybQ$EEQNmcXrSnPxIDETp/+sJbzpZ9At8IBL1EPtMb772jU	CONSUMER	t	2026-10-17 12:56:35+05:30
35	fe_test_1792222185692@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$8hqFjmij1b4O/hI70BILBA$7PQ2XpIQlrY6rpjTnKE1ZwOWFOs4vLP6cdnrDaB+/x4	CONSUMER	t	2026-10-17 12:59:45+05:30
36	fe_test_1792222395444@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$Rgqq7HFxfC8O2tP5AsMPTw$9BRRSEPH0m/hej6r8mA5Zd8Y1pBO1sXZ1PvQjU4sT50	CONSUMER	t	2026-10-17 13:03:15+05:30
37	consumer_p4_c6860d@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$5KF15A43EszJxC0qkVl5YQ$R72Kh9BgkvqIKsBWbdRx1j1cSnOrA5HEU1zbTLRZlFc	CONSUMER	t	2026-10-17 13:04:49+05:30
38	prov_p4_32a921@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$LGwYiDq8LI4y7MT6QUEU7g$cBkxKeemcOJME9XB4pY6L26YIzVriQX2GHwIa5VPnEQ	PROVIDER	t	2026-10-17 13:04:49+05:30
39	consumer_p4_09c2e2@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$6EWVs21ctEsqeJULAHE6Bw$DQ3utyFYXYaBW1V8GBPQpBNXLfvFaNQ2t43LCeRX23I	CONSUMER	t	2026-10-17 13:08:18+05:30
40	prov_p4_6a34b8@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$whahbwdMDhSlx2tne3xOKw$y6GDHLCMFkanYPq5PZUbzWh/NsOMXLGAJgF0nh++62I	PROVIDER	t	2026-10-17 13:08:18+05:30
41	fe_test_1792222791318@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$4OXl6jZLbjy5TALAcV9bvA$ZoD8pSBOe6z+qVtdFUORtA7nW+Nueg95ry9jQrpVEvg	CONSUMER	t	2026-10-17 13:09:51+05:30
42	local_provider@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$xUfS4Zu+6g+4IGKdcQCbNQ$t+bq8WGMNpCRlVMsnVqtKwj9rBXlT0T95RTLhw9OyiQ	PROVIDER	t	2026-10-17 13:24:24+05:30
43	consumer@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$PUjpk6tVwsxwE1XzA0EtxA$BsE/VOXsmaWoE3vqFNVUVbfCOj2bLuancVZIZBOnlAo	CONSUMER	t	2026-10-17 13:24:56+05:30
182	fe_test_1792748441739@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$PS+GSI9VvXOTm1oHtH2WjQ$8uOgq0aFoTlNbZacUFSLkEB1XEsIO7/WNlJ+4Kp+D84	CONSUMER	t	2026-10-23 15:10:42.207633+05:30
127	fe_test_1792428093125@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$CU5UK0NdkGEhjmt9tQOAKQ$dNRyLbZhV7QWPwkKtPV1F36bz+aEOdjph2jnwXa0R/c	CONSUMER	t	2026-10-19 22:11:33.443566+05:30
128	fe_test_1792428549004@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$wwd8c+7/wCOcixFBq4XXow$N3EJ/YoXTSzkWopjmZEQDdujfqpDnvllTgyTjSo8vHM	CONSUMER	t	2026-10-19 22:19:09.45443+05:30
129	fe_test_1792428834107@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$3AJbMpdn8F2d2muHd6E1aw$ZQtUIg1U4SQKoE9f37KOn0mUt6kENuvs61PzLDhNP0U	CONSUMER	t	2026-10-19 22:23:54.570068+05:30
130	fe_test_1792435265929@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$kxri2Y+eTCvHEoxxoX9wCQ$b9tvW5E9Ylf4DGVQnTx+M1zttySRdDRrWc68JPVstas	CONSUMER	t	2026-10-20 00:11:06.365669+05:30
140	fe_test_1792439104507@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$TuL14ydeo/LM6nLDWJpcJA$9vmNTl5c6ZjP461I7rp2oJ08vTmZGJG5PCIK+wm1qSY	CONSUMER	t	2026-10-20 01:15:04.91753+05:30
141	fe_test_1792519060684@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$ESql3Rpj39cGhFFfdhlvpw$N6KPwbcHH/2ahqNDz/pNf+FkHBhN3l8YarJEfsSY08U	CONSUMER	t	2026-10-20 23:27:41.018149+05:30
142	fe_test_1792519110874@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$fu7bV5GIaUjHMTkVgVzhcQ$QJD1XVZfhgimuN/6W+wHtCxj3LmIPF39vH0lUU0lOyo	CONSUMER	t	2026-10-20 23:28:31.194337+05:30
143	fe_test_1792519189359@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$0c75jFOM6Wtlz2hPMhrNQw$kOgyY88CfnDW70jc8JN0Qgf/yu3zEgcBpE20e1GwMDc	CONSUMER	t	2026-10-20 23:29:49.682462+05:30
213	fe_test_1792749642729@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$VOuZUhX/Q1gWX06h0cMjrQ$Jrt+XxP1JrUpxnzM/j/4/XAdC4i8dV53Zm5nSm8YvOw	CONSUMER	t	2026-10-23 15:30:43.053274+05:30
214	e2e_tester_1792749947@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$Frtt8JuiT2WlMkeZVNhQHQ$K/so33w7KzxR/buAVkM88L+46JGDcyjRyPxpoevJW7o	CONSUMER	t	2026-10-23 15:35:48.38196+05:30
215	e2e_tester_1792749985@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$ZtYqVihvFLjWwHVxO23JGQ$TSttBCkFPCpBGhp6nUU/gn0Pk5Ox4T2pzuUzfEhvCx0	CONSUMER	t	2026-10-23 15:36:25.660051+05:30
157	fe_test_1792520401257@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$QDBKIkScVF6id4lA/SVXjQ$GSQnJ5lLBPIF4AiMhVdCMw3brCucMsGDxFm46ITZq3Q	CONSUMER	t	2026-10-20 23:50:01.56555+05:30
226	fe_test_1792768598738@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$rt/K/Lj3tUVWyk8dHvnMcg$icluGp6+fB+Y0PSVt3P0emcZU7kTHB17ERVz5ikYVxg	CONSUMER	t	2026-10-23 20:46:39.068568+05:30
227	consumer_test_1792768971@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$decKalI2oPatEVNL4ITE1w$yFJPRADW0b7FUoXoBPIu1eXq+jcJXMvU1RjZ6GYS4z0	CONSUMER	t	2026-10-23 20:52:51.738913+05:30
228	consumer_test_1792769016@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$DzlwSuFVolkqtwpNq/syzA$vqAsM6EYy0Xb9QD1pvbV8DuL33aBtmQLrUc7/+/TmzY	CONSUMER	t	2026-10-23 20:53:36.747023+05:30
229	consumer_test_1792769099@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$zrF2AjKvvSSmrUUUe5WljA$rF942VJIA6NHIAlLTDgNU8YCNXfrgoPMg2MSr745O14	CONSUMER	t	2026-10-23 20:54:59.939994+05:30
230	consumer_test_1792769141@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$CvQFk4JvmUI+h1Y453nXYg$/yx/CKqjd9f/1tdu/V83mpd36N0SQiCbBrOavT9T1SM	CONSUMER	t	2026-10-23 20:55:41.999491+05:30
231	consumer_test_1792769266@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$y+4Gbr2s0EVkUhgiQCIt4g$Gr6qRyEs+IUGopDsi6i19u7T59xGJNXoVYAEqpK3WZ0	CONSUMER	t	2026-10-23 20:57:46.472872+05:30
168	fe_test_1792600781727@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$+P4i85yqGSxDKqyPWzuvqg$+KFed5PxV8493MoNi1U/V54O2fennk9OHsjaABZFSm4	CONSUMER	t	2026-10-21 22:09:42.05052+05:30
169	fe_test_1792658487719@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$IGcWCTSsAquHbI6iQY/kYQ$EAyiHmJokTcMwbUJpEV5UAO+8QrNhWLmcrxz6p2gyKk	CONSUMER	t	2026-10-22 14:11:28.00818+05:30
170	fe_test_1792659388752@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$MpqyNx3CHLuFhZEfmacBLg$TtuqHt3OGtNZgLlwtykzEpaRMQiVDSNfunggYWehXQo	CONSUMER	t	2026-10-22 14:26:30.778692+05:30
171	fe_test_1792663432982@storeshare.io	$argon2id$v=19$m=65536,t=3,p=4$JIOMhoJSZBmOFjHFiUmr6w$oOIV4BaJY4F9WP77+Hqkkjiwh2gpekfxHEYMKfQdpHQ	CONSUMER	t	2026-10-22 15:33:53.405263+05:30
\.


--
-- Name: chunk_jobs_id_seq; Type: SEQUENCE SET; Schema: public; Owner: storeshare_app
--

SELECT pg_catalog.setval('public.chunk_jobs_id_seq', 138, true);


--
-- Name: file_chunks_id_seq; Type: SEQUENCE SET; Schema: public; Owner: storeshare_app
--

SELECT pg_catalog.setval('public.file_chunks_id_seq', 39, true);


--
-- Name: files_id_seq; Type: SEQUENCE SET; Schema: public; Owner: storeshare_app
--

SELECT pg_catalog.setval('public.files_id_seq', 64, true);


--
-- Name: provider_nodes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: storeshare_app
--

SELECT pg_catalog.setval('public.provider_nodes_id_seq', 124, true);


--
-- Name: users_id_seq; Type: SEQUENCE SET; Schema: public; Owner: storeshare_app
--

SELECT pg_catalog.setval('public.users_id_seq', 231, true);


--
-- Name: app_metadata app_metadata_pkey; Type: CONSTRAINT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.app_metadata
    ADD CONSTRAINT app_metadata_pkey PRIMARY KEY (key);


--
-- Name: chunk_jobs chunk_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.chunk_jobs
    ADD CONSTRAINT chunk_jobs_pkey PRIMARY KEY (id);


--
-- Name: file_chunks file_chunks_chunk_id_key; Type: CONSTRAINT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.file_chunks
    ADD CONSTRAINT file_chunks_chunk_id_key UNIQUE (chunk_id);


--
-- Name: file_chunks file_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.file_chunks
    ADD CONSTRAINT file_chunks_pkey PRIMARY KEY (id);


--
-- Name: files files_pkey; Type: CONSTRAINT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.files
    ADD CONSTRAINT files_pkey PRIMARY KEY (id);


--
-- Name: provider_nodes provider_nodes_pkey; Type: CONSTRAINT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.provider_nodes
    ADD CONSTRAINT provider_nodes_pkey PRIMARY KEY (id);


--
-- Name: provider_nodes provider_nodes_provider_id_key; Type: CONSTRAINT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.provider_nodes
    ADD CONSTRAINT provider_nodes_provider_id_key UNIQUE (provider_id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: idx_chunk_jobs_chunk_id; Type: INDEX; Schema: public; Owner: storeshare_app
--

CREATE INDEX idx_chunk_jobs_chunk_id ON public.chunk_jobs USING btree (chunk_id);


--
-- Name: idx_chunk_jobs_node_status; Type: INDEX; Schema: public; Owner: storeshare_app
--

CREATE INDEX idx_chunk_jobs_node_status ON public.chunk_jobs USING btree (provider_node_id, status);


--
-- Name: idx_chunk_jobs_provider_node_id; Type: INDEX; Schema: public; Owner: storeshare_app
--

CREATE INDEX idx_chunk_jobs_provider_node_id ON public.chunk_jobs USING btree (provider_node_id);


--
-- Name: idx_chunk_jobs_status; Type: INDEX; Schema: public; Owner: storeshare_app
--

CREATE INDEX idx_chunk_jobs_status ON public.chunk_jobs USING btree (status);


--
-- Name: idx_file_chunks_chunk_id; Type: INDEX; Schema: public; Owner: storeshare_app
--

CREATE INDEX idx_file_chunks_chunk_id ON public.file_chunks USING btree (chunk_id);


--
-- Name: idx_file_chunks_file_id; Type: INDEX; Schema: public; Owner: storeshare_app
--

CREATE INDEX idx_file_chunks_file_id ON public.file_chunks USING btree (file_id);


--
-- Name: idx_file_chunks_node_id; Type: INDEX; Schema: public; Owner: storeshare_app
--

CREATE INDEX idx_file_chunks_node_id ON public.file_chunks USING btree (provider_node_id);


--
-- Name: idx_file_chunks_provider_node_id; Type: INDEX; Schema: public; Owner: storeshare_app
--

CREATE INDEX idx_file_chunks_provider_node_id ON public.file_chunks USING btree (provider_node_id);


--
-- Name: idx_files_owner_id; Type: INDEX; Schema: public; Owner: storeshare_app
--

CREATE INDEX idx_files_owner_id ON public.files USING btree (owner_id);


--
-- Name: idx_files_status; Type: INDEX; Schema: public; Owner: storeshare_app
--

CREATE INDEX idx_files_status ON public.files USING btree (status);


--
-- Name: idx_provider_nodes_provider_id; Type: INDEX; Schema: public; Owner: storeshare_app
--

CREATE INDEX idx_provider_nodes_provider_id ON public.provider_nodes USING btree (provider_id);


--
-- Name: idx_provider_nodes_status; Type: INDEX; Schema: public; Owner: storeshare_app
--

CREATE INDEX idx_provider_nodes_status ON public.provider_nodes USING btree (status);


--
-- Name: idx_users_email; Type: INDEX; Schema: public; Owner: storeshare_app
--

CREATE INDEX idx_users_email ON public.users USING btree (email);


--
-- Name: chunk_jobs chunk_jobs_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.chunk_jobs
    ADD CONSTRAINT chunk_jobs_file_id_fkey FOREIGN KEY (file_id) REFERENCES public.files(id) ON DELETE CASCADE;


--
-- Name: chunk_jobs chunk_jobs_provider_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.chunk_jobs
    ADD CONSTRAINT chunk_jobs_provider_node_id_fkey FOREIGN KEY (provider_node_id) REFERENCES public.provider_nodes(id) ON DELETE CASCADE;


--
-- Name: file_chunks file_chunks_file_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.file_chunks
    ADD CONSTRAINT file_chunks_file_id_fkey FOREIGN KEY (file_id) REFERENCES public.files(id) ON DELETE CASCADE;


--
-- Name: file_chunks file_chunks_provider_node_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.file_chunks
    ADD CONSTRAINT file_chunks_provider_node_id_fkey FOREIGN KEY (provider_node_id) REFERENCES public.provider_nodes(id) ON DELETE CASCADE;


--
-- Name: files files_owner_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.files
    ADD CONSTRAINT files_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: provider_nodes provider_nodes_provider_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: storeshare_app
--

ALTER TABLE ONLY public.provider_nodes
    ADD CONSTRAINT provider_nodes_provider_id_fkey FOREIGN KEY (provider_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict fDc5s6M2bzZwKVNCFrUdSf2CmnnanTj2G9FlncGudFGLWMigimAKV1BUjdadtgC

